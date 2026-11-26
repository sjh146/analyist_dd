#!/usr/bin/env python3
"""모델 top-k 기대값을 '분봉으로 보정한 순서 확률'로 전 이력에서 재는 도구.

문제: 일봉만으로는 목표(+3%)와 손절(-1%) 중 무엇이 먼저 닿았는지 모른다.
      '손절 먼저'로 가정하면 고변동 종목이 과도하게 불리해진다(2026-11-26 실측).
해법: 분봉이 있는 구간에서 **변동성 분위별 도달 확률**을 실측하고, 그 확률을
      일봉 전 이력(314일)에 적용해 기대 손익을 계산한다. 확률은 데이터가 정하고,
      가정은 '분위 내 동질' 하나만 남는다.

사용:
    python3 scripts/eval_topk_calibrated.py --panel data/panels/panel_prod200_v8.npz \
        --target 3.0 --stop 1.0 --topk 3
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

import numpy as np
import psycopg2


def load_env(path: str = "/home/jhshi/analyist_dd/.env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def pg_connect():
    load_env()
    host = os.environ.get("POSTGRES_HOST") or "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434"))
    if not os.path.exists("/.dockerenv"):
        host = "127.0.0.1" if host == "postgres" else host
        port = 5434 if port == 5432 else port
    return psycopg2.connect(host=host, port=port, dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
                            user=os.environ.get("POSTGRES_USER", "stock_user"),
                            password=os.environ.get("POSTGRES_PASSWORD", ""))


def calibrate(conn, tgt, stp, quintiles):
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, trade_date::text, open_price, high_price, low_price, close_price
        FROM market_data WHERE open_price > 0
    """)
    daily, atr = {}, defaultdict(list)
    for c, d, o, h, l, cl in cur.fetchall():
        o, h, l, cl = float(o), float(h), float(l), float(cl)
        daily[(str(c), d)] = (o, h, l, cl)
        if cl > 0:
            atr[str(c)].append((d, (h - l) / cl * 100.0))
    cur.execute("""
        SELECT stock_code, trade_date::text, time, close_price
        FROM minute_close_bars WHERE close_price > 0 ORDER BY stock_code, trade_date, time
    """)
    rows = cur.fetchall()
    cur.close()

    vol = {}
    for code, series in atr.items():
        series.sort()
        vals = [v for _, v in series[-20:]]
        if vals:
            vol[code] = float(np.mean(vals))

    bars = defaultdict(list)
    for c, d, tm, px in rows:
        bars[(str(c), d)].append((int(tm), float(px)))

    sampled = []
    for key, series in bars.items():
        code, _ = key
        q = daily.get(key)
        v = vol.get(code)
        if not q or v is None:
            continue
        o = q[0]
        ordered = sorted(series)
        first = None
        for tm, px in ordered:
            if px >= o * (1 + tgt):
                first = "target"
                break
            if px <= o * (1 - stp):
                first = "stop"
                break
        close_ret = (ordered[-1][1] / o - 1.0) if ordered else 0.0
        sampled.append((v, first, close_ret))

    sampled.sort(key=lambda r: r[0])
    n = len(sampled)
    edges = [sampled[min(n - 1, int(i * n / quintiles))][0] for i in range(quintiles)]
    table = []
    for qi in range(quintiles):
        lo = edges[qi]
        hi = edges[qi + 1] if qi + 1 < quintiles else float("inf")
        chunk = [r for r in sampled if (r[0] >= lo and r[0] < hi)]
        both = [r for r in chunk if r[1] is not None]
        if not chunk:
            table.append((lo, hi, 0.0, 0.0, 0.0))
            continue
        p_t = sum(1 for r in both if r[1] == "target") / len(both) if both else 0.0
        p_close = sum(1 for r in chunk if r[1] is None) / len(chunk)
        close_avg = np.mean([r[2] for r in chunk if r[1] is None]) if p_close else 0.0
        table.append((lo, hi, p_t, p_close, float(close_avg)))
    return table, vol, daily


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="data/panels/panel_prod200_v8.npz")
    ap.add_argument("--target", type=float, default=3.0)
    ap.add_argument("--stop", type=float, default=1.0)
    ap.add_argument("--cost", type=float, default=0.21)
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--dates-per-fold", type=int, default=10)
    ap.add_argument("--quintiles", type=int, default=5)
    args = ap.parse_args()

    conn = pg_connect()
    table, vol, daily = calibrate(conn, args.target / 100, args.stop / 100, args.quintiles)
    conn.close()
    print(f"{'분위':>3} {'ATR%구간':>14} {'목표먼저(둘다터치시)':>12} {'미도달':>7} {'미도달 종가평균':>10}")
    for i, (lo, hi, p_t, p_close, close_avg) in enumerate(table, 1):
        hi_s = "inf" if hi == float("inf") else f"{hi:.2f}"
        print(f"{i:>3} {lo:6.2f}~{hi_s:>6} {p_t:>12.2%} {p_close:>7.1%} {close_avg * 100:>9.2f}%")

    z = np.load(args.panel, allow_pickle=True)
    X = z["X"].astype(np.float32)
    dates = np.array([str(x) for x in z["dates"]])
    codes = np.array([str(x) for x in z["codes"]])

    tgt, stp = args.target, args.stop
    exp_ret = np.full(len(dates), np.nan)
    for i, (code, day) in enumerate(zip(codes.tolist(), dates.tolist())):
        q = daily.get((code, day))
        v = vol.get(code)
        if not q or v is None:
            continue
        o, h, l, cl = q
        up_hit = (h / o - 1) * 100 >= tgt
        dn_hit = (l / o - 1) * 100 <= -stp
        qi = 0
        for k, (lo, hi, _, _, _) in enumerate(table):
            if v >= lo:
                qi = k
        lo, hi, p_t, p_close, close_avg = table[qi]
        if up_hit and dn_hit:
            exp_ret[i] = p_t * tgt + (1 - p_t) * (-stp)
        elif up_hit:
            exp_ret[i] = tgt
        elif dn_hit:
            exp_ret[i] = -stp
        else:
            exp_ret[i] = (cl / o - 1) * 100 if p_close else 0.0

    label = (exp_ret > 0).astype(int)
    ok = np.isfinite(exp_ret)
    X, exp_ret, label, dates, codes = X[ok], exp_ret[ok], label[ok], dates[ok], codes[ok]

    import xgboost as xgb
    uniq = sorted(set(dates.tolist()))
    tail = uniq[-(args.folds * args.dates_per_fold):]
    chunks = [tail[i * args.dates_per_fold:(i + 1) * args.dates_per_fold] for i in range(args.folds)]
    nets = []
    for f, chunk in enumerate(chunks):
        first = min(chunk)
        tr = np.array([d < first for d in dates.tolist()])
        te = np.array([d in set(chunk) for d in dates.tolist()])
        if tr.sum() < 500 or te.sum() < 50:
            continue
        clf = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.9,
                                colsample_bytree=0.8, eval_metric="auc", tree_method="hist", random_state=42)
        clf.fit(X[tr], label[tr])
        p = clf.predict_proba(X[te])[:, 1]
        td = dates[te]
        day_vals = []
        for d in chunk:
            m = td == d
            if m.sum() < 20:
                continue
            order = np.argsort(-p[m])[:args.topk]
            day_vals.append(float(np.mean(exp_ret[te][m][order])) - args.cost)
        nets.extend(day_vals)
        if day_vals:
            print(f"fold {f+1} top{args.topk} 순기대 {np.mean(day_vals):+.3f}% · 일수 {len(day_vals)}")

    arr = np.array(nets)
    print(f"== 보정 기대값: top{args.topk} 일평균 {arr.mean():+.3f}% · t {arr.mean() / (arr.std() / np.sqrt(len(arr)) + 1e-12):+.2f} · {len(arr)}일")
    print(f"   (참고) 전 종목 평균 {exp_ret.mean() - args.cost:+.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
