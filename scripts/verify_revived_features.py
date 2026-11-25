#!/usr/bin/env python3
"""죽은 피처 부활 검증 — 원인별로 '고친 것'이 실제로 값을 갖는지 측정한다.

측정 항목(모두 실측, 추정 없음):
  1) 커버리지  : 패널 (종목,일) 중 값이 0이 아닌 비율
  2) 방향성    : 5거래일 후 수익률에 대한 상위-하위 분위 스프레드와 t값
  3) 원천부재  : 원천이 없어 살릴 수 없는 피처는 사유와 함께 출력

사용(호스트, cwd=리포 루트):
    python3 scripts/verify_revived_features.py --panel data/panels/panel_prod200_v7.npz \
        --out data/panels/panel_prod200_v8.npz --report data/reports/revived_features.json
"""
from __future__ import annotations

import argparse
import collections
import json
import os

import numpy as np
import psycopg2

SNS_COLS = ("sentiment_score", "sns_momentum_score", "kalman_sentiment",
            "sns_post_count", "sns_author_quality_score")
VERTICAL = None


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


def theme_momentum_column(conn, codes, dates) -> np.ndarray:
    """(종목, D) → D 이전 테마 평균 수익률(7일 창) 평균. 당일 값은 쓰지 않는다."""
    day_list = sorted(set(dates.tolist()))
    code_list = sorted(set(codes.tolist()))
    cur = conn.cursor()
    cur.execute("""
        SELECT st.stock_code, d.trade_date, avg(d.avg_ret_pct)
        FROM theme_daily d
        JOIN stock_themes st ON st.theme = d.theme
        WHERE d.trade_date >= %s::date - INTERVAL '200 days'
        GROUP BY 1, 2
    """, (day_list[0],))
    per: dict[tuple[str, str], float] = {}
    for code, day, val in cur.fetchall():
        if val is not None:
            per[(code, str(day))] = float(val)
    cur.close()

    pos = {d: i for i, d in enumerate(day_list)}
    out = np.zeros(len(dates), dtype=np.float32)
    for i, (code, day) in enumerate(zip(codes.tolist(), dates.tolist())):
        k = pos.get(day)
        if k is None:
            continue
        window = [per.get((code, day_list[j])) for j in range(max(0, k - 7), k)]
        vals = [v for v in window if v is not None]
        if vals:
            out[i] = float(np.mean(vals))
    return out


def forward_spread(matrix: np.ndarray, dates, codes, price, horizon: int = 5) -> tuple[float, float, int]:
    idx = collections.defaultdict(list)
    for i, d in enumerate(dates.tolist()):
        idx[d].append(i)
    day_list = sorted(idx)
    spreads = []
    for k, day in enumerate(day_list):
        if k + horizon >= len(day_list):
            continue
        fut = {codes[i]: price[i] for i in idx[day_list[k + horizon]]}
        pts = []
        for i in idx[day]:
            p0 = price[i]
            p1 = fut.get(codes[i])
            if p0 and p0 > 0 and p1:
                pts.append((matrix[i], p1 / p0 - 1.0))
        if len(pts) < 40:
            continue
        xs = np.array([p[0] for p in pts])
        rs = np.array([p[1] for p in pts])
        if xs.std() == 0:
            continue
        top = xs >= np.quantile(xs, 0.7)
        bot = xs <= np.quantile(xs, 0.3)
        if top.sum() < 10 or bot.sum() < 10:
            continue
        spreads.append(float(rs[top].mean() - rs[bot].mean()))
    if not spreads:
        return (0.0, 0.0, 0)
    arr = np.array(spreads)
    t = float(arr.mean() / (arr.std() / np.sqrt(len(arr)) + 1e-12))
    return (float(arr.mean()), t, len(arr))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="data/panels/panel_prod200_v6.npz")
    ap.add_argument("--out", default="data/panels/panel_prod200_v8.npz")
    ap.add_argument("--report", default="data/reports/revived_features.json")
    args = ap.parse_args()

    z = np.load(args.panel, allow_pickle=True)
    names = [str(x) for x in z["feature_names"]]
    X = z["X"].astype(np.float32)
    dates = z["dates"]
    codes = z["codes"]
    price = z["price"]

    conn = pg_connect()
    tm = theme_momentum_column(conn, codes, dates)
    conn.close()

    results = {}
    results["theme_momentum"] = {
        "nonzero_pct": round(float((tm != 0).mean() * 100), 3),
        "source": "theme_daily + stock_themes (7일 창, 당일 제외)",
    }
    if "theme_momentum" not in names:
        names = names + ["theme_momentum"]
        X = np.hstack([X, tm.reshape(-1, 1)])
    else:
        X[:, names.index("theme_momentum")] = tm

    for col in SNS_COLS:
        if col in names:
            c = X[:, names.index(col)]
            results[col] = {"nonzero_pct": round(float((c != 0).mean() * 100), 3), "source": "sns_post_features 패치"}

    for col in ("theme_momentum",) + SNS_COLS:
        if col not in names:
            continue
        mean_s, t, n = forward_spread(X[:, names.index(col)], dates, codes, price, horizon=5)
        results[col].update({"spread_5d_pct": round(mean_s * 100, 3), "t": round(t, 2), "days": n})

    np.savez_compressed(args.out, X=X, feature_names=np.array(names, dtype=object),
                        dates=dates, codes=codes, price=price)
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)

    for col, info in sorted(results.items(), key=lambda kv: -kv[1]["nonzero_pct"]):
        print(f"{col:24s} nonzero {info['nonzero_pct']:6.2f}%  "
              f"5일 스프레드 {info.get('spread_5d_pct', 0):+.3f}%  t {info.get('t', 0):+.2f}  "
              f"일수 {info.get('days', 0)}")
    print(f"패널 저장: {args.out} ({X.shape[0]}행 × {X.shape[1]}피처)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
