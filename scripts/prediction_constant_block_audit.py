#!/usr/bin/env python3
"""배포 예측 테이블의 '상수 블록' 감사 — ml_predictions 에 정보 없는 동일확률 덩어리가 있는가.

왜 필요한가 (2026-10-10 엔지니어 자율, 장외 틱):
  forward_live_audit(CG158)이 날짜별 confidence 분포를 재다가 두 가지를 발견했고, 여기서
  그 원인 쪽을 판다:
    ① 2026-09-22 는 2,678행 **전부 단일값 0.1429** = AUC 정의상 정확히 0.5 → 전방 AUC 희석.
    ② 정상일에도 특정 값 하나가 **373행(8.5%)** 을 차지한다(09-25 = 0.5689, 10-03 = 0.1227).
  같은 확률을 공유하는 행은 서로 **순위를 정할 수 없다**(소비자가 top-k 를 뽑으면 그 안에서는
  무작위). 그 블록의 값이 소비 문턱(0.55) 이상이면 **정보 없는 행이 선택 후보가 된다**.
  이건 성능 주장이 아니라 **생산 경로 정합성** 문제다 — 어느 날 몇 행이 상수인지 수치로 남긴다.

판정(사전 고정, 이동 금지):
  · degenerate_date : distinct confidence == 1 (그 날 전 종목 동일 = 정보 0)
  · constant_block  : 동일 confidence 를 공유하는 행수 >= max(25, 1% of n) 인 값의 집합
  · block_above_consumer_threshold : 상수 블록 값 중 >= 0.55 인 블록의 행수 합
  · 판정선 없음(측정 전용). 이 스크립트는 기준선·문턱·판정을 바꾸지 않는다.

읽기 전용(SELECT 만). 재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/prediction_constant_block_audit.py'
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime

CONSUMER_THRESHOLD = 0.55  # 트레이더 절대문턱(CG158 실측 인용 · 여기서 바꾸지 않는다)


def _connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def audit(rows_by_date: dict) -> dict:
    out = []
    for d in sorted(rows_by_date):
        vals = [float(v) for v in rows_by_date[d]]
        n = len(vals)
        cnt = Counter(vals)
        distinct = len(cnt)
        top_val, top_cnt = cnt.most_common(1)[0]
        floor = max(25, int(0.01 * n))
        blocks = [(v, c) for v, c in cnt.most_common() if c >= floor]
        above = sum(c for v, c in blocks if v >= CONSUMER_THRESHOLD)
        out.append({
            "date": d,
            "n": n,
            "distinct": distinct,
            "degenerate": distinct == 1,
            "top_value": round(top_val, 4),
            "top_count": top_cnt,
            "top_share_pct": round(100.0 * top_cnt / n, 2),
            "n_blocks": len(blocks),
            "block_rows": int(sum(c for _, c in blocks)),
            "block_rows_pct": round(100.0 * sum(c for _, c in blocks) / n, 2),
            "block_rows_above_thr": int(above),
            "blocks": [{"value": round(v, 4), "count": c} for v, c in blocks[:5]],
        })
    degen = [r for r in out if r["degenerate"]]
    worst = max(out, key=lambda r: r["block_rows_pct"]) if out else None
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "consumer_threshold": CONSUMER_THRESHOLD,
        "n_dates": len(out),
        "n_degenerate_dates": len(degen),
        "degenerate_dates": [r["date"] for r in degen],
        "worst_block_date": worst["date"] if worst else None,
        "worst_block_rows_pct": worst["block_rows_pct"] if worst else None,
        "dates": out,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-01")
    ap.add_argument("--json-out", default="/app/reports/overnight/prediction_constant_block_audit.json")
    args = ap.parse_args()

    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT prediction_date, confidence FROM ml_predictions "
        "WHERE prediction_date >= %s AND confidence IS NOT NULL ORDER BY 1",
        (args.since,),
    )
    by_date: dict = {}
    for d, c in cur.fetchall():
        by_date.setdefault(str(d), []).append(c)

    res = audit(by_date)
    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    print(f"[ok] {args.json_out}")
    print(f"dates={res['n_dates']} degenerate={res['n_degenerate_dates']} {res['degenerate_dates']}")
    print(f"{'date':<12}{'n':>6}{'distinct':>9}{'top_val':>9}{'top_cnt':>8}{'top%':>7}{'blocks':>7}{'blk%':>7}{'>=thr':>7}")
    for r in res["dates"]:
        print(f"{r['date']:<12}{r['n']:>6}{r['distinct']:>9}{r['top_value']:>9}{r['top_count']:>8}"
              f"{r['top_share_pct']:>7}{r['n_blocks']:>7}{r['block_rows_pct']:>7}{r['block_rows_above_thr']:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
