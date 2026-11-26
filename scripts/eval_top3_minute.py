#!/usr/bin/env python3
"""모델 top-k 선택을 '분봉 실제 도달 순서'로 평가한다 — 일봉 근사 편향 제거.

왜 필요한가: 일봉만 쓰면 '저가 터치 = 손절'로 가정해야 해서 고변동 종목이 과도하게 불리해진다.
분봉이 있는 11거래일(2026-11-10~11-24)에 한해, 모델 예측 상위 k종목의 실제 첫 터치 순서로
손익을 계산한다.

사용(컨테이너):
    docker exec -w /app stock_xgboost_ml python scripts/eval_top3_minute.py \
        --panel /app/app/models/wf/panel_v8_sns_theme.npz --target 3.0 --stop 1.0 --topk 3
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

import numpy as np
import psycopg2


def load_env(path: str = "/app/.env") -> None:
    for candidate in (path, "/home/jhshi/analyist_dd/.env"):
        if not os.path.exists(candidate):
            continue
        with open(candidate, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        return


def pg_connect():
    load_env()
    host = os.environ.get("POSTGRES_HOST") or "postgres"
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    return psycopg2.connect(host=host, port=port, dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
                            user=os.environ.get("POSTGRES_USER", "stock_user"),
                            password=os.environ.get("POSTGRES_PASSWORD", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/app/app/models/wf/panel_v8_sns_theme.npz")
    ap.add_argument("--target", type=float, default=3.0)
    ap.add_argument("--stop", type=float, default=1.0)
    ap.add_argument("--cost", type=float, default=0.21)
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--min-date", default="2026-11-10")
    args = ap.parse_args()

    z = np.load(args.panel, allow_pickle=True)
    X = z["X"].astype(np.float32)
    names = [str(x) for x in z["feature_names"]]
    dates = np.array([str(x) for x in z["dates"]])
    codes = np.array([str(x) for x in z["codes"]])

    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, trade_date::text, open_price, close_price
        FROM market_data WHERE trade_date >= '2026-11-01' AND open_price > 0
    """)
    daily = {(str(c), d): (float(o), float(cl)) for c, d, o, cl in cur.fetchall()}
    cur.execute("""
        SELECT stock_code, trade_date::text, time, close_price
        FROM minute_close_bars WHERE close_price > 0
        ORDER BY stock_code, trade_date, time
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    bars = defaultdict(list)
    for c, d, tm, px in rows:
        bars[(str(c), d)].append((int(tm), float(px)))

    test_days = sorted({d for (_, d) in bars if d >= args.min_date})
    if not test_days:
        print("분봉 테스트 구간 없음")
        return 2
    first_test = min(test_days)
    tr = np.array([d < first_test for d in dates.tolist()])
    if tr.sum() < 500:
        print("학습 표본 부족", int(tr.sum()))
        return 2

    tgt, stp = args.target / 100.0, args.stop / 100.0
    hit = np.zeros(len(dates), dtype=np.int8)
    for i, (code, day) in enumerate(zip(codes.tolist(), dates.tolist())):
        q = daily.get((code, day))
        if not q:
            continue
        o = q[0]
        series = bars.get((code, day))
        if series and any(px >= o * (1 + tgt) for _, px in series):
            hit[i] = 1

    import xgboost as xgb
    clf = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.9,
                            colsample_bytree=0.8, eval_metric="auc", tree_method="hist", random_state=42)
    clf.fit(X[tr], hit[tr])

    results = []
    for day in test_days:
        m = dates == day
        if m.sum() < 20:
            continue
        p = clf.predict_proba(X[m])[:, 1]
        day_codes = codes[m]
        order = np.argsort(-p)[:args.topk]
        for j in order:
            code = str(day_codes[j])
            q = daily.get((code, day))
            series = bars.get((code, day))
            if not q or not series:
                continue
            o = q[0]
            up, dn = o * (1 + tgt), o * (1 - stp)
            first, ret = None, None
            for tm, px in sorted(series):
                if px >= up:
                    first, ret = "target", args.target
                    break
                if px <= dn:
                    first, ret = "stop", -args.stop
                    break
            if first is None:
                ret = (sorted(series)[-1][1] / o - 1.0) * 100.0
                first = "close"
            results.append((day, code, round(float(p[j]), 3), first, round(ret, 3)))

    if not results:
        print("평가 표본 없음")
        return 2
    nets = np.array([r[4] for r in results]) - args.cost
    kinds = defaultdict(int)
    for r in results:
        kinds[r[3]] += 1
    print(f"선택 {len(results)}건 · 일수 {len({r[0] for r in results})} · top{args.topk}")
    print("  도달:", dict(kinds))
    print(f"  순기대(비용 {args.cost}% 차감) {nets.mean():+.3f}% · t {nets.mean() / (nets.std() / np.sqrt(len(nets)) + 1e-12):+.2f}")
    for day in sorted({r[0] for r in results}):
        day_net = [r[4] - args.cost for r in results if r[0] == day]
        print(f"   {day} {np.mean(day_net):+.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
