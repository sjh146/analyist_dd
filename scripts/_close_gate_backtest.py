#!/usr/bin/env python3
"""_close_gate_backtest.py — 종가스크리너 경로의 '게이트 비용' 실측 (프로브, 읽기 전용).

무엇을 재나
  종가스크리너(scripts/close_screener.py)를 과거 거래일별로 재생성해, 각 후보의
  **발굴일 종가 매수 → 다음 거래일 시가 매도** 수익률을 market_data 로 계산한다
  (트레이더 close 프로파일의 exit_mode=next_open 과 같은 정의).
  그리고 트레이더 게이트가 실제로 살 수 있었던 집합과 전체 집합을 비교한다:
    · R1(close)  : 상위 10개 평균 score ≥ 88.0          (runner/config.py r1_min_avg_score)
    · HEAT(close): 상위 10개 평균 당일등락 ≤ +15.0%      (r1_max_avg_pct)
  → "이중 상한이 경로를 닫는 것이 방어인가 손실인가"를 숫자로 답하기 위한 측정이다.

한계(보고에 함께 써라)
  · 진입가 근사: 실제 진입은 14:50~15:29 체결가인데 여기서는 그날 종가를 쓴다.
  · 종가스크리너는 market_data 확정 일봉을 쓰므로 D-1/D-2 발행 지연의 영향을 받는다.
  · 후보 CSV 는 재생성물이며 당시 발행본이 아니다(그날의 데이터로 다시 계산).

사용
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/_close_gate_backtest.py --days 60 --out-dir data/reports/close_gate_probe
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R1_MIN_AVG_SCORE = 88.0     # runner/config.py: r1_min_avg_score["close"]
R1_MAX_AVG_PCT = 15.0       # runner/config.py: r1_max_avg_pct
TOP_N = 10                  # R1/HEAT 는 상위 10개 평균으로 판정
PY = "/usr/bin/python3"


def pg():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", 5434)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def trading_dates(conn, start: date, end: date):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT trade_date FROM market_data "
                "WHERE trade_date BETWEEN %s AND %s ORDER BY 1", (start, end))
    return [r[0] for r in cur.fetchall()]


def price_frame(conn, start: date, end: date):
    """(stock_code, trade_date) → open/close 프레임(후보 조회용)."""
    return pd.read_sql(
        "SELECT stock_code, trade_date, open_price::float8 AS open_price, "
        "close_price::float8 AS close_price FROM market_data "
        "WHERE trade_date BETWEEN %s AND %s",
        conn, params=(start, end))


def build_candidates(dates, out_dir):
    """각 거래일의 종가스크리너를 재생성해 CSV 로 저장한다(스크리너 자체 로직 사용)."""
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for i, d in enumerate(dates, 1):
        out = os.path.join(out_dir, "close_candidates_%s_000000.csv" % d.strftime("%Y%m%d"))
        if os.path.exists(out) and os.path.getsize(out) > 0:
            made.append(out)
            continue
        cmd = [PY, os.path.join(REPO, "scripts/close_screener.py"),
               "--date", d.isoformat(), "--top-n", "20", "--output", out]
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=900)
        ok = os.path.exists(out) and os.path.getsize(out) > 0
        print("[%d/%d] %s rc=%s %s" % (i, len(dates), d, r.returncode,
                                       "OK" if ok else "후보 없음/실패"), flush=True)
        if not ok and r.returncode not in (0,):
            print((r.stderr or "")[-300:], flush=True)
        if ok:
            made.append(out)
    return made


def stats(vals):
    if not len(vals):
        return {"n": 0}
    a = np.array(vals, dtype=float)
    return {"n": int(len(a)), "win_rate": round(float((a > 0).mean() * 100), 1),
            "avg": round(float(a.mean()), 3), "median": round(float(np.median(a)), 3),
            "p10": round(float(np.percentile(a, 10)), 2),
            "p90": round(float(np.percentile(a, 90)), 2),
            "min": round(float(a.min()), 2), "max": round(float(a.max()), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="달력일 기준 최근 N일")
    ap.add_argument("--out-dir", default="data/reports/close_gate_probe")
    ap.add_argument("--skip-build", action="store_true", help="이미 생성된 CSV 만 사용")
    args = ap.parse_args()

    out_dir = args.out_dir if os.path.isabs(args.out_dir) else os.path.join(REPO, args.out_dir)
    conn = pg()
    end = trading_dates(conn, date(2000, 1, 1), date.today())[-1]          # 최신 확정 일봉
    # 시가 매도가 가능해야 하므로 마지막 신호일은 '최신 확정 일봉의 하루 전 거래일'
    all_dates = trading_dates(conn, end - timedelta(days=args.days + 10), end)
    signal_dates = all_dates[:-1]
    print("확정 일봉 최신일=%s · 신호일 %d개 (%s ~ %s)"
          % (end, len(signal_dates), signal_dates[0], signal_dates[-1]), flush=True)

    files = [] if args.skip_build else build_candidates(signal_dates, out_dir)
    files = sorted(glob.glob(os.path.join(out_dir, "close_candidates_*.csv")))
    print("후보 CSV %d개" % len(files), flush=True)

    px = price_frame(conn, signal_dates[0] - timedelta(days=5), end)
    px = px.sort_values(["stock_code", "trade_date"])
    nxt = {}
    for code, grp in px.groupby("stock_code", sort=False):
        ds = list(grp["trade_date"])
        ops = list(grp["open_price"])
        for j in range(len(ds) - 1):
            nxt[(code, ds[j])] = ops[j + 1]
    close_map = {(r.stock_code, r.trade_date): r.close_price for r in px.itertuples()}

    rows = []
    per_date = []
    for f in files:
        try:
            df = pd.read_csv(f)
        except Exception:  # noqa: BLE001
            continue
        if df.empty or "stock_code" not in df.columns:
            continue
        d = pd.to_datetime(str(df["signal_date"].iloc[0])).date() if "signal_date" in df.columns \
            else None
        if d is None:
            continue
        df = df.copy()
        df["code"] = df["stock_code"].astype(str).str.zfill(6)
        top = df.sort_values("score", ascending=False).head(TOP_N)
        avg_score = float(top["score"].mean()) if len(top) else float("nan")
        avg_pct = float(top["day_change_pct"].mean()) if "day_change_pct" in top else float("nan")
        r1_ok = bool(avg_score >= R1_MIN_AVG_SCORE)
        heat_ok = bool(avg_pct <= R1_MAX_AVG_PCT)
        per_date.append({"date": str(d), "n": int(len(df)), "avg_score_top10": round(avg_score, 2),
                         "avg_daychg_top10": round(avg_pct, 2), "r1_ok": r1_ok, "heat_ok": heat_ok,
                         "gate_open": bool(r1_ok and heat_ok)})
        for r in df.itertuples():
            sc = close_map.get((r.code, d))
            nx = nxt.get((r.code, d))
            if not sc or not nx:
                continue
            rows.append({"date": str(d), "code": r.code,
                         "score": float(getattr(r, "score", 0) or 0),
                         "day_change_pct": float(getattr(r, "day_change_pct", 0) or 0),
                         "ret_next_open_pct": (nx / sc - 1) * 100.0,
                         "gate_ok": bool(r1_ok and heat_ok)})
    tr = pd.DataFrame(rows)
    per = pd.DataFrame(per_date)
    if tr.empty:
        print("측정 가능한 행이 없습니다(후보 CSV/가격 결측)"); return 2

    flags = {r["date"]: (bool(r["r1_ok"]), bool(r["heat_ok"])) for r in per_date}
    tr["r1_ok"] = [flags.get(d, (False, False))[0] for d in tr["date"]]
    tr["heat_ok"] = [flags.get(d, (False, False))[1] for d in tr["date"]]

    summary = {
        "measured_at": pd.Timestamp.now().isoformat(),
        "signal_dates": [str(signal_dates[0]), str(signal_dates[-1])],
        "n_signal_days": int(len(signal_dates)),
        "n_rows": int(len(tr)),
        "entry_assumption": "발굴일 종가 매수(실제 14:50-15:29 근사) → 다음 거래일 시가 매도",
        "all_candidates": stats(tr["ret_next_open_pct"]),
        "gate_open_days_only": stats(tr[tr["gate_ok"]]["ret_next_open_pct"]),
        "gate_blocked_days_only": stats(tr[~tr["gate_ok"]]["ret_next_open_pct"]),
        "by_gate_reason": {
            "r1_fail_heat_ok": stats(tr[~tr["r1_ok"] & tr["heat_ok"]]["ret_next_open_pct"]),
            "r1_ok_heat_fail": stats(tr[tr["r1_ok"] & ~tr["heat_ok"]]["ret_next_open_pct"]),
            "both_fail": stats(tr[~tr["r1_ok"] & ~tr["heat_ok"]]["ret_next_open_pct"]),
        },
        "days": {"total": int(len(per)), "gate_open": int(per["gate_open"].sum()),
                 "r1_ok": int(per["r1_ok"].sum()), "heat_ok": int(per["heat_ok"].sum())},
        "avg_daychg_top10_mean": round(float(per["avg_daychg_top10"].mean()), 2),
        "avg_score_top10_mean": round(float(per["avg_score_top10"].mean()), 2),
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    tr.to_csv(os.path.join(out_dir, "trades.csv"), index=False)
    per.to_csv(os.path.join(out_dir, "days.csv"), index=False)

    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
