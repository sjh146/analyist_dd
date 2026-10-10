#!/usr/bin/env python3
"""CG159 효과 오프라인 증명 — ETF/ETN 필터가 '상수 블록'을 실제로 지우는가.

자율(장외 틱, 읽기 전용). CG159 는 "매일 약 370행(ETN 369)이 단일 상수 confidence 로
발행되고, 그 값이 소비 문턱 0.55 를 넘는다"는 감사다. 수리(배포 추론 유니버스에서 ETF/ETN
제외, 기본 OFF 플래그 `PREDICT_EXCLUDE_ETFETN`)를 배선한 뒤, **활성화하지 않고도** 그 효과를
DB 실측으로 증명한다: 상수 블록을 이루는 코드들의 **종목명**을 `is_etf_etn` 으로 분류해
"필터 ON 이면 그 블록이 0행이 되는가"를 계산한다.

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg159_filter_effect_probe.py'

판정: 상수 블록 중 ETF/ETN 비중 ≥ 95% 이면 "블록 소멸" 로 적는다(잔여 행을 함께 출력).
읽기 전용 — ml_predictions/stocks SELECT 만 한다. 아무것도 활성화하지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.dirname(_HERE), os.path.join(os.path.dirname(_HERE), "services", "xgboost-ml")):
    if _cand not in sys.path:
        sys.path.insert(0, _cand)

from app.training.universe import is_etf_etn  # noqa: E402

CONSUMER_THRESHOLD = 0.55  # 트레이더 절대문턱(CG158/CG159 인용 · 여기서 바꾸지 않는다)
OUT = "/app/reports/overnight/cg159_filter_effect.json"


def _connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT stock_code, stock_name FROM stocks")
    names = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute(
        """SELECT prediction_date::text, stock_code, confidence
             FROM ml_predictions
            WHERE confidence IS NOT NULL
            ORDER BY prediction_date, stock_code"""
    )
    by_date: dict = {}
    for d, code, conf in cur.fetchall():
        by_date.setdefault(d, []).append((code, float(conf)))
    cur.close()
    conn.close()

    rows_out = []
    for d in sorted(by_date):
        vals = by_date[d]
        n = len(vals)
        cnt = Counter(v for _, v in vals)
        top_val, top_cnt = cnt.most_common(1)[0]
        floor = max(25, int(0.01 * n))
        block = [(c, v) for c, v in vals if v == top_val] if top_cnt >= floor else []
        block_etf = [c for c, _ in block if is_etf_etn(names.get(c))]
        resid = [c for c, _ in block if not is_etf_etn(names.get(c))]
        ge_before = sum(1 for _, v in vals if v >= CONSUMER_THRESHOLD)
        ge_after = sum(
            1 for c, v in vals if v >= CONSUMER_THRESHOLD and not is_etf_etn(names.get(c))
        )
        rows_out.append({
            "date": d, "n": n, "distinct": len(cnt), "top_value": round(top_val, 4),
            "block_rows": len(block), "block_etf_etn": len(block_etf),
            "block_residual": len(resid),
            "block_etf_etn_pct": round(100.0 * len(block_etf) / len(block), 1) if block else None,
            "ge_0.55_before": ge_before, "ge_0.55_after": ge_after,
            "residual_examples": [f"{c}:{names.get(c)}" for c in resid[:5]],
        })

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "CG159 수리 플래그(PREDICT_EXCLUDE_ETFETN) 의 오프라인 효과 계산 — 활성화하지 않음",
        "consumer_threshold": CONSUMER_THRESHOLD,
        "dates": rows_out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"{'date':<12}{'n':>6}{'블록':>6}{'ETF/ETN':>8}{'잔여':>5}{'%':>7}{'≥.55전':>8}{'≥.55후':>8}")
    for r in rows_out:
        print(f"{r['date']:<12}{r['n']:>6}{r['block_rows']:>6}{r['block_etf_etn']:>8}"
              f"{r['block_residual']:>5}{str(r['block_etf_etn_pct']):>7}"
              f"{r['ge_0.55_before']:>8}{r['ge_0.55_after']:>8}")
    tot_b = sum(r["block_rows"] for r in rows_out)
    tot_e = sum(r["block_etf_etn"] for r in rows_out)
    print(f"\n합계: 상수 블록 {tot_b}행 중 ETF/ETN {tot_e}행 "
          f"({round(100.0*tot_e/tot_b,1) if tot_b else 0}%) · 잔여 {tot_b - tot_e}행")
    print(f"[ok] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
