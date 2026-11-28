"""규칙 레지스트리 — 지금 우리가 쓰는 매매 규칙을 '가설 → 측정 → 판정'으로 관리한다.

왜 필요한가: 규칙이 코드·config·크론에 흩어져 있어 "이 규칙이 검증됐나?"를 매번
처음부터 다시 확인해야 했다. 실험 500건이 결론 없이 쌓인 이유도 이것이다.
각 규칙에 상태·근거·다음 검토일을 붙여, 검토일이 지난 규칙을 매일 보고한다.

상태 정의:
  proposed  가설만 있음(측정 전)
  testing   실주문/백테스트로 검증 중
  active    검증 통과, 실사용
  rejected  기각(근거 기록)
  killed    폐기(원천·구조상 불가)

사용:
    python3 scripts/rule_registry.py            # 표 + 지연 검토 경고
    python3 scripts/rule_registry.py --json     # 기계용
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

REGISTRY = Path("/home/jhshi/analyist_dd/data/state/rule_registry.json")
JOURNAL = "/mnt/c/Users/jhshi/trader-agent/journal/trade_journal.sqlite3"

SEED = {
    "updated": "2026-11-28",
    "rules": [
        {"id": "R-EXIT-LIMIT", "name": "청산 지정가 우선 + 20초 시장가 폴백",
         "status": "testing", "since": "2026-11-28", "review": "2026-12-05",
         "evidence": "시장가 청산 28건 평균 슬리피지 -0.245%(최악 -1.005%). 지정가 전환 기대 +0.245%/건",
         "measure": "청산 체결가 vs 트리거가 평균, 폴백 비율"},
        {"id": "R-RR-075-5", "name": "손익비 0.75%/5% + 트레일 무장 3.5%",
         "status": "testing", "since": "2026-11-28", "review": "2026-12-12",
         "evidence": "분봉 10,942 종목·일 격자: 1%/3% -0.048% → 0.75%/5% +0.162%(슬리피지 반영). 승률 14.9%",
         "measure": "실현 건당 net 기대값, 40건 이상"},
        {"id": "R-LIQ-50", "name": "유동성 50억 우선(0건일 때만 10억 완화)",
         "status": "active", "since": "2026-11-27", "review": "2026-12-27",
         "evidence": "10억 우선 순서 버그로 27~45억 종목 4건 혼입·117730 매수 발생. 교체 후 50억 미만 0건",
         "measure": "후보 최소 거래대금, 체결 슬리피지"},
        {"id": "R-ATR-Q4", "name": "ATR 상위 분위 우선 진입",
         "status": "proposed", "since": "2026-11-27", "review": "2026-12-06",
         "evidence": "ATR 4분위 +0.260% vs 1분위 -0.082%(10,600 종목·일)",
         "measure": "분위 필터 적용 전후 실현 기대값 A/B"},
        {"id": "R-US-OVN", "name": "미장 오버나이트(NASDAQ/SOX z) 참조",
         "status": "proposed", "since": "2026-11-28", "review": "2027-02-15",
         "evidence": "SOX z>=1 다음날 KOSDAQ +0.43%·5%도달 26% vs z<=-1 +0.19%·23%(표본 4~5일)",
         "measure": "60세션 누적 후 IC·게이트 여부 결정"},
        {"id": "R-ENTRY-BID", "name": "진입도 bid 지정가(추격 금지)",
         "status": "proposed", "since": "2026-11-28", "review": "2026-12-06",
         "evidence": "매수는 항상 매도호가 체결 → 스프레드 절반 지불. 진입 추격은 시가 대비 평균 -1.06%(추격 아님)",
         "measure": "bid 진입 체결률·슬리피지"},
        {"id": "R-MODEL-RANK", "name": "모델 확률을 순위 결정에 반영",
         "status": "rejected", "since": "2026-11-27", "review": "2027-01-31",
         "evidence": "실서빙 IC +0.0065(t 0.41), OOS AUC 0.4742, 후보 C 5일 -2.266%",
         "measure": "라벨 재정의 후 IC>=0.03 & t>1.5 이면 재검토"},
        {"id": "R-HALT-EXIT", "name": "halt 중에도 보유 청산 감시 유지",
         "status": "active", "since": "2026-11-26", "review": "2027-01-31",
         "evidence": "halt 중 청산 감시 정지로 069510 5.5시간 방치(24,349 청산)",
         "measure": "halt 발생 시 청산 지연 시간"},
    ],
}


def load() -> dict:
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(SEED, ensure_ascii=False, indent=2), encoding="utf-8")
    return SEED


def realized_stats() -> dict:
    try:
        conn = sqlite3.connect(JOURNAL)
        row = conn.execute(
            "select count(*), avg(pnl), sum(pnl) from trades "
            "where exit_ts is not null and exit_price > 0 and coalesce(fees,0) > 0"
        ).fetchone()
        conn.close()
        n, avg, tot = row
        return {"closed": n or 0, "avg_net": float(avg or 0), "sum_net": float(tot or 0)}
    except sqlite3.Error:
        return {"closed": 0, "avg_net": 0.0, "sum_net": 0.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    reg = load()
    today = date.today()
    overdue = []
    for r in reg["rules"]:
        r["_overdue"] = (r["status"] in {"testing", "proposed"}
                         and datetime.strptime(r["review"], "%Y-%m-%d").date() <= today)
        if r["_overdue"]:
            overdue.append(r)

    if args.json:
        print(json.dumps({"rules": reg["rules"], "stats": realized_stats()}, ensure_ascii=False, indent=2))
        return 0

    st = realized_stats()
    counts = {}
    for r in reg["rules"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"규칙 {len(reg['rules'])}개 · " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"실현(net, 비용 반영) {st['closed']}건 · 건당 {st['avg_net']:+.0f}원 · 누적 {st['sum_net']:+.0f}원")
    print(f"\n{'ID':<14} {'상태':<9} {'검토일':<11} 규칙")
    for r in reg["rules"]:
        flag = " !" if r.get("_overdue") else "  "
        print(f"{r['id']:<14} {r['status']:<9} {r['review']:<11}{flag}{r['name']}")
    if overdue:
        print(f"\n검토 지난 규칙 {len(overdue)}개:")
        for r in overdue:
            print(f"  - {r['id']} {r['name']} → 측정: {r['measure']}")
    else:
        print("\n검토 지난 규칙 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
