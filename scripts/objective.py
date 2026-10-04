#!/usr/bin/env python3
"""objective.py — analyist_dd 의 '돈' 목표·봉투·게이트를 기계가 읽는 단일 진실원.

WHY (2026-10-02/03 실측): 자율 개선 루프가 사람 없이 돌려면 **모든 도구가 같은 목표 숫자**를 봐야
한다. 그런데 지금까지 목표는 문서(`docs/MONEY_GOAL.md`)에만 있었고, 승격 게이트는 AUC 를 봤다.
실측: 단일분할 AUC ↔ 다중폴드 OOS AUC 순위 상관 -0.81, AUC ↔ 순기대 상관 없음(+0.10/-0.24, n=8)
→ 대리지표를 최적화하면 돈과 어긋난다. 그래서 목표를 `config/objective.json` 한 곳에 고정하고,
파이프라인·게이트·계기판이 여기서 값을 읽는다.

사용:
  python3 scripts/objective.py show                 # 목표·봉투·게이트 요약
  python3 scripts/objective.py check                # 스키마 검증(문제 있으면 rc=2)
  python3 scripts/objective.py envelope-drift       # 트레이더 설정과 봉투 값 비교(불일치 rc=2)
  python3 scripts/objective.py promote-flags        # 파이프라인이 champion_promote 에 넘길 인자
  python3 scripts/objective.py gate set promote_require_robust 1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJECTIVE_PATH = os.environ.get("OBJECTIVE_PATH") or os.path.join(REPO, "config", "objective.json")
TRADER = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"

REQUIRED_TOP = ("version", "goal", "envelope", "gates")
REQUIRED_GOAL = ("primary_metric", "protocol", "acceptance")


class ObjectiveError(RuntimeError):
    pass


def load(path: str | None = None) -> dict:
    with open(path or OBJECTIVE_PATH, encoding="utf-8") as f:
        obj = json.load(f)
    problems = validate(obj)
    if problems:
        raise ObjectiveError("objective.json 검증 실패: " + "; ".join(problems))
    return obj


def validate(obj: dict) -> list[str]:
    """필수 키·타입만 본다(과잉 스키마로 루프를 멈추지 않는다)."""
    p: list[str] = []
    for k in REQUIRED_TOP:
        if k not in obj:
            p.append(f"최상위 '{k}' 없음")
    if "goal" in obj:
        for k in REQUIRED_GOAL:
            if k not in obj["goal"]:
                p.append(f"goal.'{k}' 없음")
        acc = obj["goal"].get("acceptance", {})
        for k in ("primary", "min_improvement_pct_over_incumbent", "min_sample_sessions"):
            if k not in acc:
                p.append(f"goal.acceptance.'{k}' 없음")
    return p


# ── 봉투 드리프트 — 목표 파일은 트레이더 설정을 '비추는' 값이다. 실제와 어긋나면 알린다 ──
# (소스 오브 트루스는 트레이더 설정이고, objective.json 은 기계가 읽는 사본이다.)
_ENVELOPE_PATTERNS = {
    "max_entries_per_day": (f"{TRADER}/runner/config.py", r"r2_max_entries_per_day\s*:\s*int\s*=\s*(\d+)"),
    "max_position_pct": (f"{TRADER}/trader_core/config.py", r"max_position_pct\s*:\s*float\s*=\s*([0-9.]+)"),
    "max_invested_pct": (f"{TRADER}/trader_core/config.py", r"max_invested_pct\s*:\s*float\s*=\s*([0-9.]+)"),
    "daily_loss_limit_pct": (f"{TRADER}/runner/config.py", r"r4_loss_limit_pct\s*:\s*float\s*=\s*([0-9.]+)"),
    "r1_max_avg_score": (f"{TRADER}/runner/config.py", r"r1_max_avg_score\s*:\s*float\s*=\s*([0-9.]+)"),
    "r1_max_avg_pct": (f"{TRADER}/runner/config.py", r"r1_max_avg_pct\s*:\s*float\s*=\s*([0-9.]+)"),
}


def envelope_drift(obj: dict) -> list[str]:
    """목표 파일의 봉투 값과 트레이더 설정이 다른 항목을 돌려준다."""
    out: list[str] = []
    for key, (path, pat) in _ENVELOPE_PATTERNS.items():
        want = obj.get("envelope", {}).get(key)
        if want is None:
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except OSError as e:
            out.append(f"{key}: 트레이더 설정을 읽지 못함({e!r})")
            continue
        m = re.search(pat, text)
        if not m:
            out.append(f"{key}: 설정에서 값을 찾지 못함({os.path.basename(path)})")
            continue
        got = float(m.group(1))
        if abs(got - float(want)) > 1e-9:
            out.append(f"{key}: objective={want} ≠ 트레이더={got}")
    return out


def promote_flags(obj: dict) -> list[str]:
    """파이프라인이 champion_promote 에 넘길 인자(게이트 정책 → CLI 플래그).

    AUC 개선폭(`--min-improvement`)은 파이프라인이 관리하므로 여기서는 **증거 게이트만** 만든다.
    """
    g = obj.get("gates", {})
    acc = obj["goal"]["acceptance"]
    flags: list[str] = []
    # 라이브 스코어 게이트는 champion_promote 안에서 **항상** 토큰으로 강제된다(플래그 불필요).
    # 여기서는 증거 게이트만 만든다 — champion_promote 가 실제로 받는 인자만 내보낸다.
    if g.get("promote_require_expectancy"):
        flags.append("--require-expectancy")
        flags.append(f"--min-expectancy-pct {acc.get('min_expectancy_pct', 0.0)}")
        flags.append(f"--min-expectancy-sessions {acc.get('min_sample_sessions', 40)}")
        flags.append(f"--min-expectancy-trades {acc.get('min_sample_trades', 30)}")
    if g.get("promote_require_robust"):
        flags.append("--require-robust")
    return flags


def set_gate(key: str, value) -> None:
    """게이트 플래그를 원자적으로 갱신한다(중간 상태로 루프가 읽지 않게)."""
    import datetime as _dt
    with open(OBJECTIVE_PATH, encoding="utf-8") as f:
        obj = json.load(f)
    obj.setdefault("gates", {})[key] = value
    obj["updated"] = _dt.date.today().isoformat()
    tmp = OBJECTIVE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, OBJECTIVE_PATH)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="analyist_dd 목표·봉투·게이트(기계용)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    sub.add_parser("check")
    sub.add_parser("envelope-drift")
    sub.add_parser("promote-flags")
    g = sub.add_parser("gate")
    g.add_argument("op", choices=("set", "get"))
    g.add_argument("key")
    g.add_argument("value", nargs="?")

    a = ap.parse_args(argv)
    try:
        obj = load()
    except (ObjectiveError, OSError, ValueError) as e:
        print(f"[objective] 읽기 실패: {e}", file=sys.stderr)
        return 2

    if a.cmd == "show":
        print(json.dumps({"goal": obj["goal"]["primary_metric"],
                          "acceptance": obj["goal"]["acceptance"],
                          "envelope": obj["envelope"],
                          "gates": obj["gates"]}, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "check":
        print("[objective] 스키마 OK")
        return 0
    if a.cmd == "envelope-drift":
        drift = envelope_drift(obj)
        if drift:
            print("[objective] 봉투 드리프트:")
            for d in drift:
                print("  -", d)
            return 2
        print("[objective] 봉투 일치(트레이더 설정과 동일)")
        return 0
    if a.cmd == "promote-flags":
        print(" ".join(promote_flags(obj)))
        return 0
    if a.cmd == "gate":
        if a.op == "get":
            print(json.dumps(obj.get("gates", {}).get(a.key), ensure_ascii=False))
            return 0
        if a.value is None:
            print("[objective] set 에는 값이 필요하다", file=sys.stderr)
            return 2
        val: object = a.value
        if a.value.lower() in ("true", "false"):
            val = a.value.lower() == "true"
        else:
            try:
                val = float(a.value)
            except ValueError:
                pass
        set_gate(a.key, val)
        print(f"[objective] gates.{a.key} = {val!r}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
