#!/usr/bin/env python3
"""ceo_decide.py — CEO 페르소나의 입력 팩·결정 기록·자기심사 (돈 기준 단일 책임자).

WHY (2026-10-04): 역할들이 각자 자기 지표에 갇혀 있다 — 엔지니어는 실험/AUC 축, 트레이더는 체결/경로,
리서처는 수집/커버리지. 각자 '바쁨'은 만들지만 **돈이 나아지는지는 아무도 책임지지 않는다**
(실측: CG95 의 +2%p 는 시장 베타였고, 널 기준선이 없어 아무도 못 잡았다).

헌장 — 이 파일이 **코드로 강제**한다(말로만 두면 지켜지지 않는다):
  결정 가능 : ① 다음에 공격할 1순위 레버 ② 실험 킬 ③ 야간 실험 쿼터 ④ 사람에게 올릴 에스컬레이션
  결정 불가 : 실주문 경로(trader-agent/**) · 봉투·게이트 값(config/objective.json) · 승격 실행
              (champion_promote) · .env · 킬스위치 · 파이프라인 본체(full_pipeline_dd.sh)
              → 이런 변경이 필요하면 결정이 아니라 **에스컬레이션**으로만 적는다(needs_human).
  의무      : 모든 결정은 근거·기대효과·측정기준을 갖춘 **기계가 읽는 기록**으로 남는다.
              '돈 지표 없이 진척 주장'은 거부된다.
  책임      : 주 1회 자기심사 — "내가 고른 레버가 돈 지표를 실제로 움직였나?"

사용
  python3 scripts/ceo_decide.py --context            # CEO 에게 줄 입력 팩(JSON)
  python3 scripts/ceo_decide.py --record d.json      # 결정 기록(검증 실패 시 rc=2, 거부)
  python3 scripts/ceo_decide.py --audit              # 자기심사 자료(지난 결정 vs 돈 지표 변화)
  python3 scripts/ceo_decide.py --show               # 현재 우선순위·최근 결정
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

DECISIONS = os.path.join(REPO, "data", "state", "ceo_decisions.jsonl")
PRIORITY = os.path.join(REPO, "data", "state", "ceo_priority.json")

# 변경이 필요한 순간 반드시 사람에게 올려야 하는 대상(금지 경로와 동일 계열).
# 여기에 걸리는 결정은 '실행 결정'이 아니라 '에스컬레이션'으로만 기록된다.
PROTECTED = ("trader-agent", "full_pipeline_dd.sh", "champion_promote", "objective.json",
             "objective.py", ".env", "kill_switch", "gate_promote_live_score",
             "envelope", "max_entries_per_day", "per_stock_pct", "max_invested_pct",
             "daily_loss_limit_pct")
ACTION_WORDS = ("변경", "수정", "적용", "실행", "올린다", "낮춘다", "바꾼", "설정", "change",
                "apply", "modify", "raise", "lower", "set ")
REQUIRED = ("lever", "why", "evidence", "experiment_quota", "kills", "escalations", "expectations")
MAX_QUOTA = 3


def _today() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def validate(decision: dict) -> list[str]:
    """결정 검증 — 반환값이 비어 있지 않으면 기록 거부(rc=2)."""
    v: list[str] = []
    for k in REQUIRED:
        if k not in decision:
            v.append("필수 필드 누락: {0}".format(k))
    if v:
        return v
    if not str(decision.get("lever") or "").strip():
        v.append("lever(1순위 레버)가 비어 있다")
    if len(str(decision.get("why") or "").strip()) < 10:
        v.append("why(근거)가 너무 짧다 — 근거 없는 결정 금지")
    ev = decision.get("evidence") or []
    if not isinstance(ev, list) or not ev:
        v.append("evidence 가 비었다 — 돈 지표/측정 근거 없이 진척 주장 금지")
    q = decision.get("experiment_quota")
    if not isinstance(q, int) or not (0 <= q <= MAX_QUOTA):
        v.append("experiment_quota 는 0~{0} 정수".format(MAX_QUOTA))
    for k in ("kills", "escalations", "expectations"):
        if not isinstance(decision.get(k), list):
            v.append("{0} 는 리스트".format(k))
    # 금지 대상에 '행동'을 지시하면 결정이 아니라 에스컬레이션이다.
    # 단 escalations 는 **정당한 통로**라 예외 — 거기서 변경을 '요청'하는 것은 허용이다(과잉 차단 금지).
    act_fields = {k: decision.get(k) for k in ("lever", "why", "expectations", "kills")}
    blob = json.dumps(act_fields, ensure_ascii=False).lower()
    for token in PROTECTED:
        if token.lower() in blob and any(w in blob for w in (t.lower() for t in ACTION_WORDS)):
            v.append("금지 대상 변경 지시 감지('{0}') → 결정이 아니라 escalations 로 올려라".format(token))
    return v


def record(decision: dict, path: str = DECISIONS) -> tuple[bool, list[str]]:
    v = validate(decision)
    if v:
        return False, v
    entry = dict(decision)
    entry.setdefault("decided_at", dt.datetime.now().isoformat(timespec="seconds"))
    entry["day"] = _today()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    pri = {"lever": entry["lever"], "experiment_quota": entry["experiment_quota"],
           "kills": entry.get("kills") or [], "escalations": entry.get("escalations") or [],
           "expectations": entry.get("expectations") or [],
           "decided_at": entry["decided_at"], "day": entry["day"]}
    tmp = PRIORITY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pri, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PRIORITY)
    return True, []


def _load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_decisions(path: str = DECISIONS) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def context_pack() -> dict:
    """CEO 에게 줄 입력 — 돈·목표·레버·실험·큐·지난 결정. 판단에 필요한 것만 담는다."""
    obj = _load_json(os.path.join(REPO, "data", "state", "objective_state.json"), {}) or {}
    reg = _load_json(os.path.join(REPO, "data", "state", "experiments.json"), {}) or {}
    que = _load_json(os.path.join(REPO, "data", "state", "improve_queue.json"), {}) or {}
    tested = (reg.get("tested") or {})
    passing = [k for k, x in tested.items() if x.get("pass")]
    best = max(((x.get("avg_pct") if x.get("avg_pct") is not None else -9), k)
               for k, x in tested.items()) if tested else (None, None)
    tasks = que.get("tasks") or []
    recent = load_decisions()[-7:]
    return {
        "when": dt.datetime.now().isoformat(timespec="seconds"),
        "goal_metric": obj.get("goal_metric"),
        "value": obj.get("value"),
        "money": obj.get("money"),
        "envelope_breaches": ((obj.get("envelope") or {}).get("breaches") or []),
        "top_lever": obj.get("top_lever"),
        "data_freshness": obj.get("data_freshness"),
        "experiments": {"tested": len(tested), "passing": passing,
                        "best_key": best[1], "best_avg_pct": best[0]},
        "queue": [{"id": t.get("id"), "state": t.get("state"), "title": t.get("title")}
                  for t in tasks][:8],
        "open_escalations": [e for d in recent for e in (d.get("escalations") or [])],
        "recent_decisions": [{"day": d.get("day"), "lever": d.get("lever"),
                              "quota": d.get("experiment_quota")} for d in recent],
        "charter": {"may_decide": ["1순위 레버", "실험 킬", "야간 실험 쿼터(0~%d)" % MAX_QUOTA,
                                   "에스컬레이션"],
                    "must_not": list(PROTECTED),
                    "rule": "금지 대상 변경은 결정이 아니라 escalations 로. 돈 지표 없는 진척 주장 금지."},
    }


def self_audit() -> dict:
    """자기심사 자료 — 지난 결정들의 레버가 돈 지표를 움직였는지 판단할 재료만 모은다(판단은 CEO 몫)."""
    obj = _load_json(os.path.join(REPO, "data", "state", "objective_state.json"), {}) or {}
    dec = load_decisions()
    hist = []
    for d in dec[-10:]:
        hist.append({"day": d.get("day"), "lever": d.get("lever"),
                     "expectations": d.get("expectations"), "quota": d.get("experiment_quota"),
                     "kills": d.get("kills")})
    return {"when": dt.datetime.now().isoformat(timespec="seconds"),
            "current_value": obj.get("value"), "current_money": obj.get("money"),
            "envelope_breaches": ((obj.get("envelope") or {}).get("breaches") or []),
            "decisions": hist,
            "question": "지난 결정의 레버가 value/money 를 실제로 움직였나? 아니면 왜 못 움직였나?",
            "rule": "움직이지 않았으면 전략 변경을 명시해야 한다(변명이 아니라 다음 수)."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CEO 페르소나 입력·기록·자기심사")
    ap.add_argument("--context", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--record", metavar="JSON_FILE")
    a = ap.parse_args(argv)

    if a.context:
        print(json.dumps(context_pack(), ensure_ascii=False, indent=2))
        return 0
    if a.audit:
        print(json.dumps(self_audit(), ensure_ascii=False, indent=2))
        return 0
    if a.show:
        print(json.dumps({"priority": _load_json(PRIORITY, {}) or {},
                          "decisions": len(load_decisions())}, ensure_ascii=False, indent=2))
        return 0
    if a.record:
        dec = _load_json(a.record)
        if not isinstance(dec, dict):
            print("결정 JSON 을 읽을 수 없다: {0}".format(a.record), file=sys.stderr)
            return 2
        ok, viol = record(dec)
        if not ok:
            print("결정 거부(헌장 위반 {0}건):".format(len(viol)), file=sys.stderr)
            for x in viol:
                print("  - {0}".format(x), file=sys.stderr)
            return 2
        print(json.dumps({"recorded": True, "lever": dec["lever"],
                          "quota": dec["experiment_quota"],
                          "kills": dec.get("kills"), "escalations": dec.get("escalations")},
                         ensure_ascii=False))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
