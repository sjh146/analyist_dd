#!/usr/bin/env python3
"""CG213 발행 confidence 의 **순위 정보** 측정 — 상위 k vs 하위 k 짝 스프레드 + 십분위 단조성.

왜 이 측정인가 (2026-11-13 00:2x 엔지니어 자율 틱, CG211/CG212 결과 → 다음 가설):
  - CG211: 발행 아티팩트 top-k Δ(+0.376%p, t 0.95, 11세션) — 부호는 양(+)이나 미검출
  - CG212: ETF/ETN 상수블록 제외는 top-k 바스켓을 **비트 단위로 바꾸지 않는다**(짝 Δ 0.000, 10/10 동점)
  - CG209: 재평가 덤프 자기 과제 Δ −0.386(t −2.03) · CG210: 배포 챔피언 OOS −0.875%p
  즉 모든 돈 지표가 'top-k 가 풀평균을 넘는다'는 형태였는데, **점수가 수익을 단조로 정렬하는가**는
  아직 안 쟀다. 상위 k 가 하위 k 를 이기지 못하면 어떤 순위 소비자(top-k·문턱·백분위)도 살릴 수 없다 —
  그 경우 남은 레버는 순위 규칙이 아니라 데이터 축이다. 이것이 이 실험의 판정 목적이다.

읽기 전용·모델 변경 없음·승격 없음. 계측기 함수는 fillable_topk_expectancy 를 그대로 재사용한다
(체결성 필터·수수료 왕복·익일시가 청산 동일 프로토콜).

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg213_spread.py \
      --jsonl /app/reports/overnight/cg212_live_all.jsonl --out /app/reports/overnight/cg213_money.json'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as F  # noqa: E402


def quintiles(rows, ks_q, rt_pct):
    """세션마다 점수 5분위 → 분위별 동일비중 gross−fee. 세션 평균과 'Q5>Q1' 세션 수를 낸다."""
    by_sess = defaultdict(list)
    for r in rows:
        by_sess[(r["fold"], r["date"])].append(r)
    per_q = defaultdict(list)
    q5_gt_q1 = 0
    n_sess = 0
    for _, rs in sorted(by_sess.items()):
        if len(rs) < ks_q:
            continue
        rs = sorted(rs, key=lambda x: -x["score"])
        n = len(rs) // ks_q
        if n < 1:
            continue
        n_sess += 1
        means = []
        for qi in range(ks_q):
            chunk = rs[qi * n:(qi + 1) * n] if qi < ks_q - 1 else rs[qi * n:]
            m = sum(x["gross_pct"] for x in chunk) / len(chunk) - rt_pct
            per_q[qi + 1].append(m)
            means.append(m)
        if means[-1] > means[0]:
            q5_gt_q1 += 1
    return {
        "n_sessions": n_sess,
        "q_mean": {str(q): round(sum(v) / len(v), 4) for q, v in sorted(per_q.items()) if v},
        "q_t": {str(q): F.stat(v).get("t") for q, v in sorted(per_q.items()) if v},
        "sessions_q5_gt_q1": q5_gt_q1,
        "q5_gt_q1_pct": round(100.0 * q5_gt_q1 / n_sess, 1) if n_sess else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="top-k vs bottom-k 짝 스프레드 + 5분위 단조성")
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--k", default="3,5")
    ap.add_argument("--exit", default="next_open", choices=F.EXITS)
    ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    a = ap.parse_args(argv)

    ks = [int(x) for x in str(a.k).split(",") if x.strip()]
    ns = SimpleNamespace(exclude_limit_up=True, max_day_chg=a.max_day_chg,
                         min_value=a.min_value, min_price=None, max_price=None)
    rt_pct = (F.FEE_BUY + F.FEE_SELL + F.TAX_SELL) * 100.0

    rows, bad = F.load_rows(a.jsonl, a.tag)
    if not rows:
        print(f"[FAIL] 행 없음 {a.jsonl}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    import pandas as pd
    dmin = min(r["date"] for r in rows)
    dmax = max(r["date"] for r in rows)
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * (a.horizon + 2))).date()
    conn = F.db_connect()
    series = F.price_series(conn, codes, d0, d1)
    enriched, dropped = F.enrich(rows, series, a.horizon, a.exit)
    fa, skipped = F.apply_filters(enriched, ns, True)
    pool = F.pool_series(fa, rt_pct)
    top = F.baskets(fa, ks, rt_pct, reverse=False)
    bot = F.baskets(fa, ks, rt_pct, reverse=True)

    out = {
        "metric_name": "fillable_topk_spread",
        "exit": a.exit, "horizon": a.horizon, "ks": ks, "row_tag": a.tag,
        "fee_roundtrip_pct": round(rt_pct, 4),
        "rows": {"loaded": len(rows), "enriched": len(enriched), "dropped_price": dropped,
                 "filtered_out": skipped, "bad_lines": bad},
        "n_sessions_fillable": len(pool),
        "pool_median_fillable": int(sorted(
            [sum(1 for r in fa if (r["fold"], r["date"]) == k) for k in pool])[len(pool) // 2]) if pool else 0,
        "conditions": {"fillable": {"k": {}}},
    }
    for k in ks:
        out["conditions"]["fillable"]["k"][str(k)] = {
            "top": F.stat(list(top[k].values())),
            "bottom": F.stat(list(bot[k].values())),
            "pool": F.stat(list(pool.values())),
            "spread_top_minus_bottom": F.paired_stats(top[k], bot[k]),
            "top_halves": F.halves(top[k]),
            "bottom_halves": F.halves(bot[k]),
        }
    out["quintile5"] = quintiles(fa, 5, rt_pct)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"exit={a.exit} h={a.horizon} 세션 {out['n_sessions_fillable']} · 풀 중앙값 "
          f"{out['pool_median_fillable']} · 수수료 왕복 {rt_pct:.3f}%p · 필터탈락 {skipped}행")
    print(f"{'k':>3} {'top%':>8} {'bottom%':>8} {'pool%':>8} {'spread':>8} {'t':>6} {'pos/neg/ty':>11}")
    for k in ks:
        b = out["conditions"]["fillable"]["k"][str(k)]
        sp = b["spread_top_minus_bottom"]
        print(f"{k:>3} {b['top'].get('mean', float('nan')):>8.3f} "
              f"{b['bottom'].get('mean', float('nan')):>8.3f} "
              f"{b['pool'].get('mean', float('nan')):>8.3f} "
              f"{sp.get('delta_mean', float('nan')):>8.3f} {sp.get('t', float('nan')):>6.2f} "
              f"{str(sp.get('pos'))+'/'+str(sp.get('neg'))+'/'+str(sp.get('ties')):>11}")
    q = out["quintile5"]
    print(f"[5분위] 세션 {q['n_sessions']} · " +
          " ".join(f"Q{qi} {v:+.3f}" for qi, v in q["q_mean"].items()) +
          f" · Q5>Q1 세션 {q['sessions_q5_gt_q1']}/{q['n_sessions']} ({q['q5_gt_q1_pct']}%)")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
