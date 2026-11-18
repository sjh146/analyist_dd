#!/usr/bin/env python3
"""공매도 원천(krx_short_selling) 단변량 스크리너 — 패널 빌드 없이 DB 조인만으로 edge 유무 판정.

왜: 2026-11-18 실측으로 krx_short_selling 이 115거래일(2026-05-26~)·약 220종목/일·short_ratio
결측 0 으로 **살아 있는 원천**이 됐다(과거엔 supply_market_features 의 short_selling_ratio 가
비영 0.24% 로 죽어 있었다). 데이터 축 재개 조건(CG73: 패널을 덮는 원천 + 신규 피처 ≥5종)의
1단계 — 패널 npz 에 (code,date) 조인해 **단변량 AUC·날짜별 IC** 를 먼저 본다.
패널 빌드(1~2시간) 전에 이 5분짜리 검사로 채택 여부를 가른다.

라벨은 스윕과 동일하게 '날짜내 분위 q=0.30'(상위 30% = 1). 피처는 as-of 안전하게 **T-1 지연**
(당일 공매도 값은 장 마감 후 확정 → 당일 종가 시점에 알 수 없음).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np


def db_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", 5434)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def auc(pairs):
    """pairs: [(y, score)] → AUC (Mann-Whitney, ties 0.5)."""
    pos = [p for y, p in pairs if y == 1]
    neg = [p for y, p in pairs if y != 1]
    if len(pos) < 3 or len(neg) < 3:
        return None
    allv = sorted(pairs, key=lambda t: t[1])
    ranks = [0.0] * len(allv)
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][1] == allv[i][1]:
            j += 1
        r = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[k] = r
        i = j + 1
    s = sum(ranks[k] for k in range(len(allv)) if allv[k][0] == 1)
    return (s - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/app/app/models/wf/panel_200liq_ffill.npz")
    ap.add_argument("--q", type=float, default=0.30, help="라벨 분위(상위 q=1)")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--lag", type=int, default=1, help="공매도 값 as-of 지연(거래일)")
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--json-out", default="/app/reports/overnight/short_screen.json")
    args = ap.parse_args()

    z = np.load(args.panel, allow_pickle=True)
    codes = np.array([str(c).zfill(6) for c in z["codes"]])
    dates = np.array([str(d) for d in z["dates"]])
    price = np.asarray(z["price"], dtype=float)
    _ds = sorted(set(dates.tolist()))
    print(f"panel rows={len(codes)} dates={len(_ds)} {_ds[0]}~{_ds[-1]}")

    # 코드별 날짜 정렬 → 미래 h일 수익률(라벨) 과 자기 과거 참조용 인덱스
    order = np.lexsort((dates, codes))
    codes_s, dates_s, price_s = codes[order], dates[order], price[order]
    per_code = defaultdict(list)   # code -> [(date, price, row_index)]
    for idx in range(len(codes_s)):
        per_code[codes_s[idx]].append((dates_s[idx], price_s[idx], order[idx]))

    # (code, date) -> fwd 수익률
    fwd = {}
    for code, rows in per_code.items():
        for i in range(len(rows) - args.horizon):
            p0, p1 = rows[i][1], rows[i + args.horizon][1]
            if p0 > 0 and p1 > 0:
                fwd[(code, rows[i][0])] = p1 / p0 - 1.0

    # 라벨: 날짜별 상위 q
    by_date = defaultdict(list)
    for (code, d), r in fwd.items():
        by_date[d].append((code, r))
    label = {}
    for d, lst in by_date.items():
        if len(lst) < 20:
            continue
        vals = sorted(r for _, r in lst)
        k = max(1, int(round(len(vals) * (1 - args.q))))
        thr = vals[-k]
        for code, r in lst:
            label[(code, d)] = 1 if r >= thr else 0

    # 공매도 시계열 → (code) -> {date: short_ratio} (거래일 순서는 패널 날짜 순서 사용)
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("select stock_code, trade_date, short_ratio, short_value, total_volume "
                "from krx_short_selling where trade_date >= %s order by stock_code, trade_date",
                (args.since,))
    raw = defaultdict(dict)
    for sc, td, sr, sv, tv in cur.fetchall():
        raw[str(sc).zfill(6)][str(td)] = (float(sr) if sr is not None else None,
                                          float(sv) if sv is not None else 0.0,
                                          float(tv) if tv is not None else 0.0)
    conn.close()

    feats = {"short_ratio": {}, "short_ratio_ma5": {}, "short_ratio_chg5": {}, "short_value_ratio": {}}
    for code, series in raw.items():
        ds = sorted(series)
        sr = [series[d][0] for d in ds]
        for i, d in enumerate(ds):
            # T-1 지연: 패널 날짜가 d 이상일 때 쓸 값은 d 의 **전일** 값 → 여기서는 (date -> 값) 표를
            # 만들되, 조회 시 lag 만큼 앞선 날짜를 쓴다.
            feats["short_ratio"][(code, d)] = sr[i]
            win = [x for x in sr[max(0, i - 4): i + 1] if x is not None]
            feats["short_ratio_ma5"][(code, d)] = sum(win) / len(win) if win else None
            prev = [x for x in sr[max(0, i - 5): i] if x is not None]
            if win and prev:
                pm = sum(prev) / len(prev)
                feats["short_ratio_chg5"][(code, d)] = (sum(win) / len(win) - pm)
            sv, tv = series[d][1], series[d][2]
            feats["short_value_ratio"][(code, d)] = (sv / tv) if tv > 0 else None

    # 지연 적용: 패널 날짜 D 의 값 = 공매도 데이터의 D-lag 번째 이전 관측
    out = {"panel": args.panel, "q": args.q, "horizon": args.horizon, "lag": args.lag,
           "since": args.since, "features": {}}
    panel_dates = sorted(set(dates))
    for fname, table in feats.items():
        date_map = defaultdict(list)  # date -> [(y, val)]
        covered = 0
        total = 0
        for (code, d), y in label.items():
            if d < args.since:
                continue
            total += 1
            # 자기 코드의 관측 날짜 목록에서 d 이하 lag 번째 이전 날짜
            series = raw.get(code)
            if not series:
                continue
            ds = sorted(series)
            import bisect
            pos = bisect.bisect_right(ds, d) - 1 - (args.lag - 1) - 1 + 1  # = bisect_right - lag
            pos = bisect.bisect_right(ds, d) - args.lag
            if pos < 0:
                continue
            dd = ds[pos]
            val = table.get((code, dd))
            if val is None:
                continue
            covered += 1
            date_map[d].append((y, val))
        date_aucs = []
        for d, pairs in sorted(date_map.items()):
            a = auc(pairs)
            if a is not None:
                date_aucs.append(a)
        if not date_aucs:
            out["features"][fname] = {"n_dates": 0, "note": "표본 없음"}
            continue
        m = sum(date_aucs) / len(date_aucs)
        sd = float(np.std(date_aucs, ddof=1)) if len(date_aucs) > 1 else 0.0
        se = sd / (len(date_aucs) ** 0.5) if len(date_aucs) > 1 else None
        out["features"][fname] = {
            "n_dates": len(date_aucs), "coverage_rows": covered, "label_rows": total,
            "coverage_pct": round(100.0 * covered / max(1, total), 2),
            "date_auc_mean": round(m, 4), "date_auc_sd": round(sd, 4),
            "t_vs_0.5": round((m - 0.5) / se, 2) if se else None,
            "auc_min": round(min(date_aucs), 4), "auc_max": round(max(date_aucs), 4),
            "sign_return_ic": None,
        }
        print(f"{fname:22s} dates={len(date_aucs):3d} cov={out['features'][fname]['coverage_pct']:5.1f}% "
              f"AUC={m:.4f} sd={sd:.4f} t={(m - 0.5) / se if se else float('nan'):+.2f}")

    with open(args.json_out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved:", args.json_out)


if __name__ == "__main__":
    sys.exit(main())
