#!/usr/bin/env python3
"""CG207 근거 수집 — 소비 문턱(0.55)을 통과하는 '상수 블록'(같은 확률 덩어리) 행이
실제 매수 선택을 얼마나 차지하고, 그들의 실현 순수익이 얼마인가.

왜 이 측정인가 (2026-11-12 엔지니어 자율 틱):
  prediction_constant_block_audit(CG159)는 '상수 블록이 하루 ~370행(8.5%)이고 값이 0.5689'라는
  **발행측 사실**만 남긴다. 승인 요청에는 '그래서 돈이 얼마나 새는가'가 없어서 지금까지 보류됐다.
  여기서는 같은 데이터로 **선택·실현수익**을 잰다:
    · 문턱 0.55 이상 행 중 상수 블록 비중(선택 잠식률)
    · 블록 vs 비블록의 순수익(%p, 수수료 왕복 0.21%p 차감 · 진입=다음 거래일 시가 · 청산=t+5 종가)
    · 체결 불가(다음 시가가 전일 종가 대비 +25% 이상 갭) 행은 제외 — 살 수 없는 가격은 세지 않는다.
  판정선 없음(측정 전용). 기준선·문턱·게이트를 바꾸지 않는다.

읽기 전용(SELECT). 재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg207_block_evidence.py'
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime

CONSUMER_THRESHOLD = 0.55   # 트레이더 절대문턱 — CG159/CG158 과 같은 값(여기서 바꾸지 않는다)
FEE_RT_PCT = 0.21           # 왕복 수수료(%p) — fillable_expectancy 기본값
GAP_CAP_PCT = 25.0          # 다음 시가 갭 상한(이 이상이면 매수 불가로 제외)
HORIZON = 5                 # t+5 종가 청산


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
    ap.add_argument("--days", type=int, default=25, help="최근 예측 날짜 수")
    ap.add_argument("--json-out", default="/app/reports/overnight/cg207_block_evidence.json")
    a = ap.parse_args(argv)

    import psycopg2.extras
    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT DISTINCT prediction_date FROM ml_predictions "
                "ORDER BY prediction_date DESC LIMIT %s", (a.days,))
    dates = [r["prediction_date"] for r in cur.fetchall()]
    if not dates:
        print("[err] ml_predictions 비어 있음")
        return 2
    dmin, dmax = min(dates), max(dates)

    cur.execute("SELECT stock_code, prediction_date, confidence, created_at "
                "FROM ml_predictions WHERE prediction_date BETWEEN %s AND %s",
                (dmin, dmax))
    preds = cur.fetchall()
    codes = sorted({p["stock_code"] for p in preds})

    cur.execute("SELECT stock_code, trade_date, open_price, close_price "
                "FROM market_data WHERE stock_code = ANY(%s::text[]) "
                "AND trade_date BETWEEN %s AND %s + INTERVAL '20 days' "
                "ORDER BY stock_code, trade_date", (codes, dmin, dmax))
    px = cur.fetchall()

    series = defaultdict(list)
    for r in px:
        series[r["stock_code"]].append((r["trade_date"], float(r["open_price"] or 0),
                                        float(r["close_price"] or 0)))

    def fwd(code, d):
        """진입=다음 거래일 시가, 청산=d 이후 5번째 거래일 종가. 체결 불가면 None."""
        s = series.get(code) or []
        idx = next((i for i, x in enumerate(s) if x[0] == d), None)
        if idx is None or idx + 1 >= len(s):
            return None
        d0_close = s[idx][2]
        if d0_close <= 0:
            return None
        entry = s[idx + 1][1]
        if entry <= 0:
            return None
        gap = (entry / d0_close - 1.0) * 100.0
        if gap >= GAP_CAP_PCT:          # 살 수 없는 갭(상한가 부근)
            return None
        if idx + 1 + HORIZON - 1 >= len(s):
            return None
        exit_px = s[idx + 1 + HORIZON - 1][2]
        if exit_px <= 0:
            return None
        return (exit_px / entry - 1.0) * 100.0 - FEE_RT_PCT

    by_date = defaultdict(list)
    for p in preds:
        by_date[p["prediction_date"]].append(
            (p["stock_code"], float(p["confidence"]), p["created_at"]))

    rows = []
    for d in sorted(by_date):
        vals = [c for _, c, _ in by_date[d]]
        n = len(vals)
        cnt = Counter(vals)
        floor = max(25, int(0.01 * n))
        block_vals = {v for v, c in cnt.items() if c >= floor}
        sel = [(code, c) for code, c, _ in by_date[d] if c >= CONSUMER_THRESHOLD]
        top10 = sorted(by_date[d], key=lambda x: -x[1])[:10]
        blk = [x for x in sel if x[1] in block_vals]
        non = [x for x in sel if x[1] not in block_vals]
        r_blk = [r for r in (fwd(c, d) for c, _ in blk) if r is not None]
        r_non = [r for r in (fwd(c, d) for c, _ in non) if r is not None]
        rows.append({
            "date": str(d),
            "n_published": n,
            "n_selected_ge_thr": len(sel),
            "n_block_selected": len(blk),
            "block_share_of_selected_pct": round(100.0 * len(blk) / len(sel), 2) if sel else None,
            "n_top10_block": sum(1 for x in top10 if x[1] in block_vals),
            "block_block_values": sorted(round(v, 4) for v in block_vals)[:5],
            "fillable_block": len(r_blk),
            "fillable_nonblock": len(r_non),
            "net_block_mean_pct": round(sum(r_blk) / len(r_blk), 4) if r_blk else None,
            "net_nonblock_mean_pct": round(sum(r_non) / len(r_non), 4) if r_non else None,
        })

    def _agg(key):
        xs = [r[key] for r in rows if r[key] is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "consumer_threshold": CONSUMER_THRESHOLD,
        "fee_round_trip_pct": FEE_RT_PCT,
        "entry": "next_trading_day_open",
        "exit": f"close_at_t+{HORIZON}",
        "unfillable_rule": f"entry gap >= +{GAP_CAP_PCT}% 제외",
        "n_dates": len(rows),
        "dates_with_selection": sum(1 for r in rows if r["n_selected_ge_thr"]),
        "mean_block_share_of_selected_pct": _agg("block_share_of_selected_pct"),
        "mean_net_block_pct": _agg("net_block_mean_pct"),
        "mean_net_nonblock_pct": _agg("net_nonblock_mean_pct"),
        "dates": rows,
    }
    os.makedirs(os.path.dirname(a.json_out), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print(f"기간 {rows[0]['date']}~{rows[-1]['date']} · 날짜 {len(rows)}")
    print(f"문턱 {CONSUMER_THRESHOLD} 통과 선택 중 상수블록 평균 비중 "
          f"{summary['mean_block_share_of_selected_pct']}%")
    print(f"순수익(체결필터·수수료 {FEE_RT_PCT}%p 포함): 블록 {summary['mean_net_block_pct']}%p "
          f"vs 비블록 {summary['mean_net_nonblock_pct']}%p")
    for r in rows[-8:]:
        print(f"  {r['date']} 발행{r['n_published']:5d} 선택{r['n_selected_ge_thr']:4d} "
              f"블록{r['n_block_selected']:4d}({r['block_share_of_selected_pct']}%) "
              f"top10블록{r['n_top10_block']:2d} | 블록 {r['net_block_mean_pct']} vs "
              f"비블록 {r['net_nonblock_mean_pct']}")
    print(f"[ok] {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
