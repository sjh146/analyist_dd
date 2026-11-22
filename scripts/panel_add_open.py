#!/usr/bin/env python3
"""패널 npz 에 **익일 시가(open)** 열을 부착한다 (CG430, 2026-11-22 엔지니어).

왜 필요한가
-----------
지금까지 모든 학습 라벨은 `close(t) → close(t+h)` 수익이다. 그런데 돈 지표(체결가능
순기대)는 **익일 시가 진입**이다(실주문 가능 시점). h5 라벨 수익의 약 1/5 은 진입 전
오버나이트 갭인데, 이 갭은 우리가 살 수 없는 구간이다 → 학습 목표에 살 수 없는 성분이
섞여 있고, 모델은 그 성분을 예측하는 데 용량을 쓴다(CG420 실측: 진입 시점만 바꿔도
Δ 1.388%p → 1.216%p, t 2.38 → 2.06 로 바뀐다).

이 스크립트는 패널의 (code,date) 격자에 market_data.open_price 를 붙여 새 npz 로 저장한다.
`price`(종가) 는 손대지 않고 그대로 복사한다 → 기존 경로는 비트 동일(회귀 없음).

계약
----
- 매핑 키: (stock_code.zfill(6), trade_date) — 패널 dates/codes 와 정확히 일치해야 한다.
- 검증: 같은 격자의 market_data.close_price 와 패널 price 의 최대 상대오차를 찍는다.
  0 이 아니면 정렬이 어긋난 것이므로 **실패(rc=2)** 로 끝낸다(조용한 오정렬 금지).
- open 이 0/결측인 행은 NaN 으로 둔다 → 라벨 결측(학습 제외). 대체값을 만들지 않는다.

실행(컨테이너 안에서):
  python -u scripts/panel_add_open.py \
     --in /app/app/models/wf/panel_prod200_v6.npz \
     --out /app/app/models/wf/panel_prod200_v6o.npz \
     --report /app/reports/overnight/panel_open_patch.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd


def db_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", 5434)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    z = np.load(a.src, allow_pickle=True)
    keys = list(z.keys())
    dates = [str(d) for d in z["dates"]]
    codes = [str(c).zfill(6) for c in z["codes"]]
    price = z["price"].astype(float)
    dmin, dmax = min(dates), max(dates)
    uniq = sorted(set(codes))
    print(f"panel={a.src} rows={len(dates)} codes={len(uniq)} range={dmin}~{dmax}", flush=True)

    conn = db_connect()
    q = ("SELECT stock_code, trade_date, open_price, close_price "
         "FROM market_data WHERE stock_code = ANY(%s::text[]) "
         "AND trade_date BETWEEN %s AND %s")
    px = pd.read_sql(q, conn, params=[uniq, dmin, dmax])
    conn.close()
    px["stock_code"] = px["stock_code"].astype(str).str.zfill(6)
    px["trade_date"] = px["trade_date"].astype(str)
    m = {(r.stock_code, r.trade_date): (r.open_price, r.close_price) for r in px.itertuples()}

    op = np.full(len(dates), np.nan)
    close_db = np.full(len(dates), np.nan)
    for i, (c, d) in enumerate(zip(codes, dates)):
        v = m.get((c, d))
        if v is None:
            continue
        o, cl = v
        try:
            o = float(o)
            if o > 0:
                op[i] = o
        except (TypeError, ValueError):
            pass
        try:
            close_db[i] = float(cl)
        except (TypeError, ValueError):
            pass

    ok = np.isfinite(close_db) & np.isfinite(price) & (price != 0)
    n_ok = int(ok.sum())
    max_rel = float(np.max(np.abs(close_db[ok] - price[ok]) / np.abs(price[ok]))) if n_ok else None
    rep = {
        "src": a.src, "dst": a.dst, "rows": int(len(dates)), "codes": len(uniq),
        "date_range": [dmin, dmax],
        "mapped_rows": int(np.isfinite(close_db).sum()),
        "mapped_frac": round(float(np.isfinite(close_db).mean()), 4),
        "open_frac": round(float(np.isfinite(op).mean()), 4),
        "close_vs_panel_price_max_rel": max_rel,
        "keys_in_src": keys,
    }
    print(json.dumps(rep, ensure_ascii=False), flush=True)
    if max_rel is None or max_rel > 1e-9:
        print("실패: 패널 price 와 market_data.close_price 정렬이 어긋났다 — 중단", flush=True)
        return 2
    if rep["open_frac"] < 0.95:
        print(f"경고: open 커버리지 {rep['open_frac']:.1%} < 95% (라벨 결측이 커진다)", flush=True)

    out = {k: z[k] for k in keys if k != "open"}
    out["open"] = op
    os.makedirs(os.path.dirname(a.dst), exist_ok=True)
    np.savez_compressed(a.dst, **out)
    print(f"저장: {a.dst} (keys={sorted(out.keys())})", flush=True)
    if a.report:
        os.makedirs(os.path.dirname(a.report), exist_ok=True)
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
