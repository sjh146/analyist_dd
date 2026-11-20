#!/usr/bin/env python3
"""R8(급락 후 매수)의 기간 불안정 원인 분석 — 왜 상반기만 통했나.

WHY(2026-11-20): R8 은 전체 초과 +0.79%(t 6.8)인데 상반기 +2.14% / 하반기 -1.55% 였다.
기간을 가르는 **조건변수**를 찾아야 "어떤 상황에서만 켠다"는 규칙으로 쓸 수 있다.
검정하는 조건: ① 지수 5일 낙폭 ② 지수 20일선 대비 위치 ③ 당일 지수 낙폭 크기
④ 종목 낙폭 깊이 ⑤ 거래대금(유동성) ⑥ 월.

사용:
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 python3 scripts/r8_conditioning.py \
      --from 2026-01-02 --to 2026-11-19 --json-out data/reports/r8_conditioning.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rule_expectancy as rx  # noqa: E402

COST = 0.0061
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _stat(df: pd.DataFrame, col: str = "fwd1") -> dict:
    r = (df[col] - COST).to_numpy(dtype=float)
    if r.size < 10:
        return {"n": int(r.size), "mean": None, "t": None, "win": None}
    m = float(np.mean(r))
    sd = float(np.std(r, ddof=1))
    t = m / (sd / np.sqrt(r.size)) if sd > 0 else None
    return {"n": int(r.size), "mean": round(m * 100, 4),
            "t": round(t, 2) if t is not None else None,
            "win": round(float(np.mean(r > 0)) * 100, 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2026-01-02")
    ap.add_argument("--to", dest="d_to", default="2026-11-19")
    ap.add_argument("--index-code", default="KOSDAQ")
    ap.add_argument("--drop", type=float, default=-0.08, help="종목 낙폭 임계")
    ap.add_argument("--panic", type=float, default=-0.015, help="지수 낙폭 임계")
    ap.add_argument("--json-out", default=os.path.join(HERE, "data/reports/r8_conditioning.json"))
    args = ap.parse_args(argv)

    conn = rx._conn()
    df = rx.add_forward(rx.load_prices(conn, args.d_from, args.d_to, None, "STOCK"))
    idx = rx.load_index(conn, args.d_from, args.d_to, args.index_code)
    conn.close()

    idx = idx.copy()
    # load_index 는 dt/idx_ret 만 돌려준다(닫힌 연결 재사용). ma20 은 별도 조회가 필요하므로
    # 여기서는 5일 누적 낙폭만 쓴다 — 기간 분리를 설명하기에 충분하고 look-ahead 가 없다.
    idx["idx_ret5"] = idx["idx_ret"].rolling(5).sum()

    base = df[(df["value"] >= 3e8) & (df["close"] >= 1000) & df["fwd1"].notna()]
    base = base.merge(idx, on="dt", how="left")

    r8 = base[(base["r_day"] <= args.drop) & (base["idx_ret"] <= args.panic)].copy()
    panic_base = base[base["idx_ret"] <= args.panic]

    out = {"window": [args.d_from, args.d_to], "n_events": int(len(r8)),
           "overall": _stat(r8), "baseline_panic": _stat(panic_base), "groups": {}}

    # ① 지수 5일 낙폭
    r8["idx5_bucket"] = pd.cut(r8["idx_ret5"], [-1, -0.06, -0.03, 0, 1],
                               labels=["<=-6%", "-6~-3%", "-3~0%", ">0%"])
    # ③ 당일 지수 낙폭
    r8["idx1_bucket"] = pd.cut(r8["idx_ret"], [-1, -0.04, -0.025, args.panic],
                               labels=["<=-4%", "-4~-2.5%", "-2.5~-1.5%"])
    # ④ 종목 낙폭 깊이
    r8["drop_bucket"] = pd.cut(r8["r_day"], [-1, -0.20, -0.15, -0.10, args.drop],
                               labels=["<=-20%", "-20~-15%", "-15~-10%", "-10~-8%"])
    # ⑤ 유동성
    r8["liq_bucket"] = pd.cut(r8["value"], [0, 1e9, 5e9, 2e10, 1e12],
                              labels=["3~10억", "10~50억", "50~200억", "200억+"])
    # ⑥ 월
    r8["month"] = r8["dt"].dt.strftime("%m")

    for col in ("idx5_bucket", "idx1_bucket", "drop_bucket", "liq_bucket", "month"):
        out["groups"][col] = {str(k): _stat(g) for k, g in r8.groupby(col, observed=True)}

    # 반기 교차(핵심): 지수 5일 낙폭 버킷 × 반기
    r8["half"] = np.where(r8["dt"] < "2026-07-01", "H1", "H2")
    cross = {}
    for (half, bk), g in r8.groupby(["half", "idx5_bucket"], observed=True):
        cross[f"{half}|{bk}"] = _stat(g)
    out["groups"]["half_x_idx5"] = cross

    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print("R8 사건 %d건 · 전체 %s" % (len(r8), out["overall"]))
    print("패닉 기준선 %s" % out["baseline_panic"])
    for col, groups in out["groups"].items():
        print("== %s" % col)
        for k, v in groups.items():
            if v["n"]:
                print("   %-14s n=%5d mean=%+7.3f%% t=%5s win=%5s" % (
                    k, v["n"], v["mean"], v["t"], v["win"]))
    print("[claim] r8_conditioning -> %s events=%d" % (os.path.basename(args.json_out), len(r8)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
