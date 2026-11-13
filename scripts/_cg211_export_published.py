#!/usr/bin/env python3
"""CG211 발행 예측 내보내기 — 트레이더가 **실제로 소비한** ml_predictions 를 덤프 스키마로 변환.

왜 이 측정인가 (2026-11-12 23:0x 엔지니어 자율 틱):
  지금까지의 모든 돈 지표는 **재평가 덤프**(champion_robust_eval --dump-all)로 측정됐다.
  그런데 트레이더가 사는 것은 재평가가 아니라 발행 테이블(ml_predictions)이다. 발행 유니버스는
  학습 유니버스와 다르고(ETF/ETN 상수블록 370행/일, CG159·CG207), 절대문턱 0.55 소비자는 그 블록을
  선택한다. 즉 '발행 아티팩트 자체'의 체결성 순기대는 아직 한 번도 측정되지 않았다.
  여기서는 ml_predictions 를 fillable_topk_expectancy 스키마 jsonl 로 내보내기만 한다(판정 없음).
  판정은 기존 계측기(파일 하단 사용법)가 널(세션 풀평균) 대비로 낸다.

읽기 전용(SELECT). 재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg211_export_published.py \
      --days 25 --out /app/reports/overnight/cg211_live_pub.jsonl'
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict


def _connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=25, help="최근 발행 날짜 수")
    ap.add_argument("--tag", default="live_pub", help="jsonl 의 exp 태그")
    ap.add_argument("--out", default="/app/reports/overnight/cg211_live_pub.jsonl")
    ap.add_argument("--exclude-block", action="store_true",
                    help="날짜별 '상수 블록'(같은 confidence 값이 그날 발행수의 1%% 이상·최소 25건) 행 제외")
    a = ap.parse_args(argv)

    import psycopg2.extras
    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT DISTINCT prediction_date FROM ml_predictions "
                "ORDER BY prediction_date DESC LIMIT %s", (a.days,))
    dates = sorted(r["prediction_date"] for r in cur.fetchall())
    if not dates:
        print("[err] ml_predictions 비어 있음")
        return 2

    cur.execute("SELECT stock_code, prediction_date, confidence FROM ml_predictions "
                "WHERE prediction_date BETWEEN %s AND %s AND confidence IS NOT NULL",
                (dates[0], dates[-1]))
    preds = cur.fetchall()

    codes = sorted({p["stock_code"] for p in preds})
    cur.execute("SELECT DISTINCT stock_code FROM market_data "
                "WHERE stock_code = ANY(%s::text[]) AND trade_date BETWEEN %s AND %s "
                "AND close_price IS NOT NULL AND close_price > 0",
                (codes, dates[0], dates[-1]))
    priced = {r["stock_code"] for r in cur.fetchall()}

    by_date = defaultdict(list)
    for p in preds:
        by_date[p["prediction_date"]].append((p["stock_code"], float(p["confidence"])))

    # 상수 블록 판정은 CG207 과 **같은 규칙**을 쓴다(같은 값이 그날 발행수의 1% 이상·최소 25건).
    block_vals_by_date = {}
    for d, pairs in by_date.items():
        cnt = defaultdict(int)
        for _, c in pairs:
            cnt[c] += 1
        floor = max(25, int(0.01 * len(pairs)))
        block_vals_by_date[d] = {v for v, n in cnt.items() if n >= floor}

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    n_rows = n_priced = n_block = 0
    with open(a.out, "w", encoding="utf-8") as f:
        for d in dates:
            for code, conf in by_date[d]:
                is_block = conf in block_vals_by_date[d]
                if is_block:
                    n_block += 1
                    if a.exclude_block:
                        continue
                f.write(json.dumps({"exp": a.tag, "fold": 0, "date": str(d),
                                    "code": code, "y_true": None,
                                    "y_pred": conf}, ensure_ascii=False) + "\n")
                n_rows += 1
                n_priced += 1 if code in priced else 0

    print(f"[cg211] 발행일 {len(dates)}개({dates[0]}~{dates[-1]}) · 행 {n_rows} · "
          f"예측일 당일 가격행 보유 종목 {n_priced} ({100.0 * n_priced / max(n_rows, 1):.1f}%)"
          f" · 상수블록 {n_block}행" +
          (" [제외됨]" if a.exclude_block else ""))
    print(f"[cg211] 날짜별 발행수: " + ", ".join(f"{d}:{len(by_date[d])}" for d in dates[-5:]))
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
