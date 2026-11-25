#!/usr/bin/env python3
"""단타 전용 라벨 실험 — '장중에 목표가 도달했는가'를 예측한다.

기존 5일 상대수익 라벨은 단타(분~시간)와 정의가 다르다. 여기서는 일봉 시·고·종가로
다음 라벨을 만든다:
    hit = (당일 고가 / 시가 - 1) >= target_pct
그리고 상위 k종목을 시가 진입했다고 보고 손익을 근사한다:
    hit  → +target_pct
    miss → max(stop_pct, 종가/시가 - 1)
여기서 왕복 비용을 뺀 값이 순기대값이다.

사용(호스트):
    python3 scripts/exp_daytrade_intraday_label.py --panel data/panels/panel_prod200_v8.npz \
        --target 1.5 --stop -1.0 --topk 3 --folds 6 --dates-per-fold 10
"""
from __future__ import annotations

import argparse
import json
import os

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


def daily_bars(codes, start, end):
    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, trade_date::text, open_price, high_price, low_price, close_price
        FROM market_data
        WHERE stock_code = ANY(%s) AND trade_date BETWEEN %s AND %s
          AND open_price > 0 AND high_price > 0 AND close_price > 0
    """, (list(codes), start, end))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    out = {}
    for code, day, o, h, l, c in rows:
        out[(str(code), str(day))] = (float(o), float(h), float(l), float(c))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="data/panels/panel_prod200_v8.npz")
    ap.add_argument("--target", type=float, default=1.5)
    ap.add_argument("--stop", type=float, default=-1.0)
    ap.add_argument("--cost", type=float, default=0.21)
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--dates-per-fold", type=int, default=10)
    ap.add_argument("--report", default="data/reports/exp_daytrade_intraday.json")
    args = ap.parse_args()

    z = np.load(args.panel, allow_pickle=True)
    X = z["X"].astype(np.float32)
    names = [str(x) for x in z["feature_names"]]
    dates = np.array([str(x) for x in z["dates"]])
    codes = np.array([str(x) for x in z["codes"]])
    price = np.asarray(z["price"], dtype=np.float64)

    day_sorted = sorted(dates.tolist())
    bars = daily_bars(sorted(set(codes.tolist())), day_sorted[0], day_sorted[-1])
    day_index = {d: i for i, d in enumerate(day_sorted)}
    hit = np.zeros(len(dates), dtype=np.int8)
    payoff = np.zeros(len(dates), dtype=np.float64)
    valid = np.zeros(len(dates), dtype=bool)
    for i, (code, day) in enumerate(zip(codes.tolist(), dates.tolist())):
        k = day_index.get(day)
        if k is None or k + 1 >= len(day_sorted):
            continue
        nxt = day_sorted[k + 1]
        b = bars.get((code, nxt))
        if not b:
            continue
        o, h, l, c = b
        if o <= 0:
            continue
        up = (h / o - 1.0) * 100.0
        dn = (c / o - 1.0) * 100.0
        hit[i] = 1 if up >= args.target else 0
        payoff[i] = args.target if up >= args.target else max(args.stop, dn)
        valid[i] = True

    X, dates, codes, hit, payoff = X[valid], dates[valid], codes[valid], hit[valid], payoff[valid]
    print(f"rows {len(hit):,} · hit률 {hit.mean():.3f} · 평균 페이오프 {payoff.mean():+.3f}% · "
          f"비용차감 {payoff.mean() - args.cost:+.3f}%")

    from sklearn.metrics import roc_auc_score
    import xgboost as xgb

    uniq = sorted(set(dates.tolist()))
    tail = uniq[-(args.folds * args.dates_per_fold):]
    chunks = [tail[i * args.dates_per_fold:(i + 1) * args.dates_per_fold] for i in range(args.folds)]
    aucs, nets, hits = [], [], []
    for f, chunk in enumerate(chunks):
        first = min(chunk)
        tr = np.array([d < first for d in dates.tolist()])
        te = np.array([d in set(chunk) for d in dates.tolist()])
        if tr.sum() < 500 or te.sum() < 50:
            continue
        clf = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.05,
                                subsample=0.9, colsample_bytree=0.8, eval_metric="auc",
                                tree_method="hist", random_state=42)
        clf.fit(X[tr], hit[tr])
        p = clf.predict_proba(X[te])[:, 1]
        try:
            aucs.append(float(roc_auc_score(hit[te], p)))
        except ValueError:
            pass
        td = dates[te]
        day_pay = []
        for d in chunk:
            m = td == d
            if m.sum() < 20:
                continue
            order = np.argsort(-p[m])[:args.topk]
            day_pay.append(float(np.mean(payoff[te][m][order])) - args.cost)
            hits.append(float(np.mean(hit[te][m][order])))
        if day_pay:
            nets.extend(day_pay)
        print(f"fold {f+1} test {int(te.sum())} auc {aucs[-1]:.4f} · top{args.topk} 순기대 "
              f"{np.mean(day_pay):+.3f}% · 적중 {np.mean(hits[-len(day_pay):]):.3f}")

    res = {
        "target_pct": args.target, "stop_pct": args.stop, "cost_pct": args.cost, "topk": args.topk,
        "base_hit_rate": round(float(hit.mean()), 4),
        "base_net_pct": round(float(payoff.mean() - args.cost), 3),
        "auc_mean": round(float(np.mean(aucs)), 4) if aucs else None,
        "picked_net_pct": round(float(np.mean(nets)), 3) if nets else None,
        "picked_net_t": round(float(np.mean(nets) / (np.std(nets) / np.sqrt(len(nets)) + 1e-12)), 2) if len(nets) > 1 else None,
        "picked_hit_rate": round(float(np.mean(hits)), 3) if hits else None,
        "days": len(nets),
    }
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
