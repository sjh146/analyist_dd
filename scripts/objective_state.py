#!/usr/bin/env python3
"""objective_state.py — 자율 루프가 읽는 '목표 상태' 한 장(읽기 전용 계산 + 상태 파일 기록).

WHY (2026-10-03): 자율 개선 루프가 사람 없이 돌려면 매일 아침 **같은 숫자 한 벌**을 봐야 한다:
목표 지표(체결 가능 OOS 순기대)의 현재 값, 돈(실현손익·자본 사용), 봉투 위반 여부,
가장 큰 손실원(1순위), 데이터 신선도. 흩어진 산출물(수익 계기판·증거 파일·DB·피드)을
한 파일(`data/state/objective_state.json`)로 모아 루프·경보·보고가 여기만 보게 한다.

사용:
  python3 scripts/objective_state.py --print          # 상태 JSON 을 stdout 으로
  python3 scripts/objective_state.py --write           # data/state/objective_state.json 기록
  (봉투 위반이 있으면 rc=2 — 크론 경보가 그 신호를 쓴다)
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.environ.get("OBJECTIVE_STATE_PATH") or os.path.join(REPO, "data", "state", "objective_state.json")
sys.path.insert(0, os.path.join(REPO, "scripts"))
from objective import load as load_objective  # noqa: E402


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def latest_scoreboard(repo: str = REPO):
    files = sorted(glob.glob(os.path.join(repo, "reports", "money", "scoreboard_*.json")))
    if not files:
        return None, None
    return files[-1], _read_json(files[-1])


def model_evidence(repo: str, name: str):
    """모델 디렉토리의 증거 파일(robust_oos.json) — 순기대·AUC·표본."""
    p = os.path.join(repo, "services", "xgboost-ml", "app", "models", name, "robust_oos.json")
    d = _read_json(p)
    if d is None:
        return {"present": False, "path": p}
    d["present"] = True
    d["path"] = p
    return d


def data_freshness(repo: str = REPO):
    """데이터 신선도 신호(DB 없이 파일 기준)."""
    out: dict = {}
    feed = os.path.join(repo, "data", "feed", "screener_latest.json")
    d = _read_json(feed)
    if d:
        gen = str(d.get("generated_at") or "")
        out["feed_generated_at"] = gen
        try:
            t = dt.datetime.fromisoformat(gen.replace("Z", "+00:00"))
            out["feed_age_h"] = round((dt.datetime.now(t.tzinfo) - t).total_seconds() / 3600, 1)
        except ValueError:
            out["feed_age_h"] = None
    for name, rel in (("screener_stats", "data/reports/screener_stats_measured.json"),
                      ("swing_probe", "data/reports/swing_path_probe.json"),
                      ("close_gap_history", "data/reports/close_path_gap_history.jsonl")):
        p = os.path.join(repo, rel)
        out[f"{name}_mtime"] = (dt.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")
                               if os.path.exists(p) else None)
    return out


def envelope_check(obj: dict, sb: dict | None) -> dict:
    """봉투(위험 한도) 사용률과 위반 — 트레이더 설정을 objective.json 사본으로 검사한다."""
    env = obj.get("envelope", {})
    limits = {"max_entries_per_day": env.get("max_entries_per_day", 3),
              "per_stock_pct": env.get("max_position_pct", 0.10),
              "max_invested_pct": env.get("max_invested_pct", 0.30),
              "daily_loss_limit_pct": env.get("daily_loss_limit_pct", 0.03)}
    out = {"limits": limits, "used": {}, "breaches": []}
    if not sb:
        out["breaches"].append("수익 계기판 없음 — 실현손익·자본 사용을 확인할 수 없다")
        return out
    cap = sb.get("capital_utilization") or {}
    real = sb.get("realized") or {}
    out["used"] = {"equity_krw": cap.get("equity"), "invested_pct_now": cap.get("used_pct_now"),
                   "peak_concurrent": (cap.get("peak") or {}).get("concurrent"),
                   "closed_trades": (real.get("cum") or {}).get("closed"),
                   "realized_pnl_krw": (real.get("cum") or {}).get("pnl")}
    equity = cap.get("equity") or 0
    used_pct = cap.get("used_pct_now")
    if used_pct is not None and used_pct > limits["max_invested_pct"] * 100:
        out["breaches"].append(f"총 투자비중 {used_pct}% > 한도 {limits['max_invested_pct'] * 100}%")
    pnl = (real.get("cum") or {}).get("pnl")
    if equity and pnl is not None and pnl / equity < -limits["daily_loss_limit_pct"]:
        out["breaches"].append(f"누적 실현손익 {pnl}원 < 일 손실한도 {limits['daily_loss_limit_pct'] * 100}%×자본")
    if real.get("fees_unbooked_flag"):
        out["breaches"].append("수수료 미계상 — 과거 청산은 브로커 API(CpTd6032)가 당일만 제공해 "
                               "복구 불가(추정으로 채우지 않음). 신규 청산부터 16:05 틱이 자동 계상")
    return out


def build(repo: str = REPO, obj: dict | None = None) -> dict:
    obj = obj or load_objective()
    sb_path, sb = latest_scoreboard(repo)
    champ = model_evidence(repo, "champion")
    cand = model_evidence(repo, "champion_cand")
    env = envelope_check(obj, sb)
    verdict = ((sb or {}).get("verdict") or {})
    top1 = verdict.get("top1") or {}
    state = {
        "updated": dt.datetime.now().isoformat(timespec="seconds"),
        "goal_metric": obj["goal"]["primary_metric"],
        "acceptance": obj["goal"]["acceptance"],
        "gates_policy": obj.get("gates", {}),
        "value": {
            "champion_expectancy_pct": champ.get("expectancy_pct"),
            "champion_sessions": champ.get("n_sessions"),
            "champion_robust_auc": champ.get("robust_auc"),
            "candidate_expectancy_pct": cand.get("expectancy_pct"),
            "candidate_robust_auc": cand.get("robust_auc"),
            "evidence_present": {"champion": bool(champ.get("present")), "candidate": bool(cand.get("present"))},
            "note": "순기대는 체결성 필터·수수료 반영 OOS 값(%p/세션). 증거가 없으면 승격도 없다.",
        },
        "money": {
            "realized_pnl_krw": (env.get("used") or {}).get("realized_pnl_krw"),
            "closed_trades": (env.get("used") or {}).get("closed_trades"),
            "equity_krw": (env.get("used") or {}).get("equity_krw"),
            "open_positions": (sb or {}).get("open_state", {}).get("positions_bridge"),
            "scoreboard": sb_path,
        },
        "envelope": env,
        "top_lever": {"id": top1.get("id"), "title": top1.get("title"),
                      "krw": top1.get("krw"), "evidence": top1.get("evidence")},
        "data_freshness": data_freshness(repo),
    }
    return state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="자율 루프용 목표 상태(목표·돈·봉투·1순위 손실원)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--out", default=STATE_PATH)
    a = ap.parse_args(argv)
    try:
        state = build()
    except Exception as e:                       # noqa: BLE001 — 상태 생성 실패는 루프를 멈추지 않는다
        print(f"[objective_state] 생성 실패: {e!r}", file=sys.stderr)
        return 3
    if a.write:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        tmp = a.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, a.out)
    if a.do_print or not a.write:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    breaches = state["envelope"]["breaches"]
    if breaches:
        print("[objective_state] 봉투 경고: " + "; ".join(breaches), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
