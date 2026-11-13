#!/usr/bin/env python3
"""panel_liq_probe.py — 유동성(거래대금) 횡단면 신호를 **패널 유니버스 안에서** 잰다.

왜 필요한가 (2026-11-13, CG255):
  돈 지표(CG249~CG254)에서 '거래대금 하위'가 SNS 관측 풀에서 Δ+1.20%p(t 3.38)를 냈지만
  전 시장으로 넓히면 Δ+0.29%p(t 2.15)·절대 순기대는 음(−)이었다(CG251). 모델이 랭크하는
  유니버스(panel_*.npz)는 그 사이 어딘가다 → **패널 유니버스 안에서 같은 측정을 한 번 더**
  해야 '피처로 넣을 가치가 있는가'를 판정할 수 있다. 패널 재빌드(수 시간) 전 게이트.

계산 규칙:
  · 같은 날짜 안에서만 순위상관(Spearman) — 날짜 고정효과 제거.
  · fwd1 = 다음 종가/오늘 종가 − 1, fwd5 = 5거래일 뒤 종가/오늘 종가 − 1 (같은 종목 시계열).
  · 체결성 필터: |당일 등락| ≥ max_day_chg 이거나 거래대금 < min_value 인 행은 후보에서 제외.
  · top-k: 거래대금 **하위**(invert) k 종목 평균 fwd 수익 − 같은 날 풀 평균. 수수료 왕복 0.21%p
    는 양쪽에 동일 적용되므로 Δ 에서 상쇄되지만 절대값(topk_net)에는 차감한다.
  · 날짜별 값의 평균·std·t = mean/(std/√n) · 양(+) 날짜 비율 · 앞/뒤 절반 분할.

사용(컨테이너, cwd=/app):
  python -u scripts/panel_liq_probe.py --panel /app/app/models/wf/panel_prod200.npz \
      --money-k 5,10 --min-value 3000000000 --json-out /app/reports/overnight/cg256_panel_liq.json
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from statistics import mean, pstdev

import numpy as np
import psycopg2


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def spearman(xs, ys):
    n = len(xs)
    if n < 5:
        return None

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def tstat(vals):
    n = len(vals)
    if n < 3:
        return None, None
    m = mean(vals)
    sd = pstdev(vals) * (n / (n - 1)) ** 0.5
    if sd == 0:
        return m, None
    return m, m / (sd / n ** 0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--money-k", default="5,10")
    ap.add_argument("--invert", default="1", help="1=거래대금 하위가 top (기본)")
    ap.add_argument("--min-value", type=float, default=3e9)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--fee", type=float, default=0.21)
    ap.add_argument("--min-stocks", type=int, default=30)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    p = np.load(a.panel, allow_pickle=True)
    codes = sorted({str(c) for c in p["codes"].tolist()})
    dates = sorted({str(d)[:10] for d in p["dates"].tolist()})
    print(f"[panel] {a.panel} codes={len(codes)} dates={len(dates)} ({dates[0]}~{dates[-1]})")

    con = connect()
    cur = con.cursor()
    cur.execute(
        """
        SELECT stock_code, trade_date, close_price, trading_value
        FROM market_data
        WHERE stock_code = ANY(%s) AND trade_date BETWEEN %s AND %s
          AND close_price IS NOT NULL AND trading_value IS NOT NULL
        ORDER BY stock_code, trade_date
        """,
        (codes, dates[0], dates[-1]),
    )
    by_code = defaultdict(list)
    for c, d, px, tv in cur.fetchall():
        by_code[c].append((str(d)[:10], float(px), float(tv)))
    con.close()

    # 종목별 시계열 인덱스 → 전방수익
    rows = []  # (date, code, tv, chg, fwd1, fwd5)
    for c, seq in by_code.items():
        n = len(seq)
        for i, (d, px, tv) in enumerate(seq):
            chg = None
            if i > 0 and seq[i - 1][1]:
                chg = (px / seq[i - 1][1] - 1.0) * 100.0
            f1 = (seq[i + 1][1] / px - 1.0) * 100.0 if i + 1 < n and px else None
            f5 = (seq[i + 5][1] / px - 1.0) * 100.0 if i + 5 < n and px else None
            rows.append((d, c, tv, chg, f1, f5))

    by_date = defaultdict(list)
    for r in rows:
        if r[0] in set(dates):
            by_date[r[0]].append(r)

    out = {"panel": a.panel, "dates": len(by_date), "codes": len(codes),
           "min_value_krw": a.min_value, "fee_roundtrip_pct": a.fee,
           "max_day_chg_pct": a.max_day_chg, "ic": {}, "money": {}}

    for h, idx in (("fwd1", 4), ("fwd5", 5)):
        ics, ns = [], []
        for d in sorted(by_date):
            cand = [r for r in by_date[d]
                    if r[idx] is not None and r[3] is not None
                    and abs(r[3]) < a.max_day_chg and r[2] >= a.min_value]
            if len(cand) < a.min_stocks:
                continue
            ic = spearman([np.log10(r[2]) for r in cand], [r[idx] for r in cand])
            if ic is not None:
                ics.append(ic)
                ns.append(len(cand))
        m, t = tstat(ics)
        out["ic"][h] = {"n_dates": len(ics), "mean_ic": round(m, 4) if m is not None else None,
                        "t": round(t, 2) if t else None,
                        "pos_dates_pct": round(100.0 * sum(1 for x in ics if x > 0) / len(ics), 1) if ics else None,
                        "avg_stocks": round(mean(ns), 1) if ns else None}
        print(f"[ic] log(trading_value) vs {h}: IC {out['ic'][h]}")

    for h, idx in (("fwd1", 4), ("fwd5", 5)):
        for k in [int(x) for x in a.money_k.split(",")]:
            deltas, tops, pools = [], [], []
            for d in sorted(by_date):
                cand = [r for r in by_date[d]
                        if r[idx] is not None and r[3] is not None
                        and abs(r[3]) < a.max_day_chg and r[2] >= a.min_value]
                if len(cand) < max(a.min_stocks, 2 * k):
                    continue
                cand.sort(key=lambda r: r[2], reverse=bool(int(a.invert)))
                sel = cand[:k]
                top = mean(r[idx] for r in sel) - a.fee
                pool = mean(r[idx] for r in cand) - a.fee
                tops.append(top)
                pools.append(pool)
                deltas.append(top - pool)
            m, t = tstat(deltas)
            if m is None:
                continue
            half = len(deltas) // 2
            s1, s2 = mean(deltas[:half]), mean(deltas[half:])
            rec = {"n_dates": len(deltas), "delta_mean_pct": round(m, 4),
                   "std": round(pstdev(deltas), 4), "t": round(t, 2) if t else None,
                   "pos_dates_pct": round(100.0 * sum(1 for x in deltas if x > 0) / len(deltas), 1),
                   "split_half_pct": [round(s1, 4), round(s2, 4)],
                   "topk_net_pct": round(mean(tops), 4), "pool_net_pct": round(mean(pools), 4)}
            out["money"][f"{h}_k{k}"] = rec
            print(f"[money] {h} k={k}: {rec}")

    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)
        print("저장:", a.json_out)


if __name__ == "__main__":
    main()
