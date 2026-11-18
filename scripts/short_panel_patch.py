#!/usr/bin/env python3
"""패널 npz 의 죽은 공매도 컬럼을 실데이터로 채운다 (행·순서·다른 컬럼 불변).

배경(2026-11-18 실측): 패널 217피처에 short_selling_ratio · short_interest_ratio 가 이미 있으나
supply_market_features 경유라 비영 0.24% = 사실상 상수 0. 반면 krx_short_selling 은
115거래일(2026-05-26~)·일평균 220종목·short_ratio 결측 0% 로 살아 있다.
단변량 스크린(scripts/_short_feature_screen.py): short_ratio 날짜 AUC 0.5333(t 4.59) ·
short_ratio_ma5 0.5368(t 4.67). 이 스크립트는 그 신호를 패널 학습 입력으로 옮긴다.

규칙:
- as-of 지연 필수: 패널 날짜 D 에는 **D 이전 관측(기본 1거래일 전)** 값을 쓴다.
  당일 공매도 통계는 장 마감 후 확정이라 D 종가 시점에 알 수 없다.
- 원천에 없는 (종목,날짜)는 기존 값(=0)을 유지한다(결측을 새 정보로 만들지 않는다).
- 행·순서·다른 컬럼·feature_names 는 그대로. patched 여부를 npz 메타 대신 파일명/로그로 남긴다.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
from collections import defaultdict

import numpy as np

# 패널 컬럼 -> krx_short_selling 에서 만들 값
TARGETS = {
    "short_selling_ratio": "short_ratio",        # 공매도 거래량 / 거래량 (%)
    "short_interest_ratio": "short_value_ratio",  # 공매도 거래대금 / 거래대금 (%) — 잔고 미수집 대체
}


def db_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", 5434)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="/app/app/models/wf/panel_200liq_ffill.npz")
    ap.add_argument("--out", dest="out", default="/app/app/models/wf/panel_200liq_short.npz")
    ap.add_argument("--lag", type=int, default=1, help="as-of 지연(관측일 기준 거래일 수)")
    ap.add_argument("--since", default="2026-05-01")
    ap.add_argument("--json-out", default="/app/reports/overnight/short_panel_patch.json")
    args = ap.parse_args()

    z = np.load(args.inp, allow_pickle=True)
    X = np.array(z["X"], dtype=np.float32, copy=True)
    names = [str(x) for x in z["feature_names"]]
    codes = np.array([str(c).zfill(6) for c in z["codes"]])
    dates = np.array([str(d) for d in z["dates"]])
    price = np.asarray(z["price"], dtype=float)

    idx = {n: [i for i, x in enumerate(names) if x == n] for n in TARGETS}
    missing = {n: i for n, i in idx.items() if not i}
    print("target cols:", {n: i for n, i in idx.items()})

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("select stock_code, trade_date, short_ratio, short_value, total_volume "
                "from krx_short_selling where trade_date >= %s order by stock_code, trade_date",
                (args.since,))
    series = defaultdict(dict)
    for sc, td, sr, sv, tv in cur.fetchall():
        series[str(sc).zfill(6)][str(td)] = (float(sr) if sr is not None else None,
                                            (float(sv) / float(tv)) if (tv or 0) > 0 else None)
    conn.close()
    print("source stocks:", len(series))

    stats = {n: {"filled": 0, "kept": 0, "uniq": set()} for n in TARGETS}
    for row in range(X.shape[0]):
        code, d = codes[row], dates[row]
        s = series.get(code)
        if not s:
            continue
        ds = sorted(s)
        pos = bisect.bisect_left(ds, d) - args.lag   # d 이전(또는 lag 만큼 앞) 마지막 관측
        if pos < 0:
            continue
        val_src = s[ds[pos]]
        for col, which in TARGETS.items():
            if missing.get(col):
                continue
            v = val_src[0] if which == "short_ratio" else val_src[1]
            if v is None:
                stats[col]["kept"] += 1
                continue
            for j in idx[col]:
                X[row, j] = v
            stats[col]["filled"] += 1
            stats[col]["uniq"].add(round(v, 6))

    np.savez_compressed(args.out, X=X, feature_names=z["feature_names"], dates=z["dates"],
                        codes=z["codes"], price=price)
    out = {"in": args.inp, "out": args.out, "lag": args.lag, "rows": int(X.shape[0]),
           "missing_cols": list(missing),
           "targets": {n: {"filled": s["filled"], "kept": s["kept"],
                           "uniq_values": len(s["uniq"]),
                           "nonzero_pct": round(100.0 * s["filled"] / X.shape[0], 2)}
                       for n, s in stats.items()}}
    with open(args.json_out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out["targets"], ensure_ascii=False))
    print("saved:", args.out, args.json_out)


if __name__ == "__main__":
    main()
