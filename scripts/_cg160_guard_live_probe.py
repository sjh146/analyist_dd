#!/usr/bin/env python3
"""CG160 퇴화일 가드 — **실 DB** 대조 프로브(읽기 전용).

가드의 순수 함수(`app.inference.day_guard`)를 `ml_predictions` 실데이터에 적용해
'가드가 플래그한 날짜' == `scripts/forward_live_audit.py` 의 퇴화 날짜(n_distinct==1)인지 확인한다.
합성 입력이 아니라 **실제 발행분**에서 규약대로 동작함을 증명하는 것이 목적이다.

실행: docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg160_guard_live_probe.py'
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/app")

from app.inference.day_guard import confidence_uniqueness, degenerate_reason  # noqa: E402


def main() -> int:
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT prediction_date, confidence FROM ml_predictions WHERE confidence IS NOT NULL"
    )
    by_date = defaultdict(list)
    for d, c in cur.fetchall():
        by_date[str(d)[:10]].append({"confidence": c})
    conn.close()

    per_date, flagged = [], []
    for d in sorted(by_date):
        stats = confidence_uniqueness(by_date[d])
        reason = degenerate_reason(stats)
        rec = {"date": d, "reason": reason, **stats}
        per_date.append(rec)
        if reason:
            flagged.append(rec)

    out = {
        "probe": "cg160_guard_live_probe",
        "metric": "degenerate_day_guard",
        "n_dates": len(per_date),
        "flagged_dates": flagged,
        "flagged_share": round(len(flagged) / len(per_date), 4) if per_date else None,
        "per_date": per_date,
    }
    path = "/app/reports/cg160_guard_live_probe.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"날짜 {out['n_dates']} · 플래그 {len(flagged)} ({out['flagged_share']})")
    for r in flagged:
        print(f"  [퇴화] {r['date']} {r['reason']:12s} n={r['n']:5d} distinct={r['n_distinct']:4d} "
              f"최빈값={r['top_value']} 비중={r['top_share']}")
    print("증거:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
