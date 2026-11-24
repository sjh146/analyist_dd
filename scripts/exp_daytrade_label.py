#!/usr/bin/env python3
"""단타 라벨(다음 거래일 시가→종가) vs h5 상대수익 라벨의 walk-forward 비교 실험.

패널: /app/app/models/wf/panel_prod200_idn.npz (200종목 × 314거래일, 피처 235)
단타 라벨: y_raw = close(T+1)/open(T+1)-1 - 0.0021(왕복비용 0.21%), y = (y_raw>0),
           T+1 은 market_data 에서 해당 종목의 다음 거래일.
대조 라벨: t+5 종가수익률에서 그날 표본 중앙값을 뺀 상대수익 > 0 (exp_panel_wf.py 와 동일).
평가: 확장창 5폴드(폴드당 10거래일), purge 는 라벨별(단타 1일, h5 5일),
      폴드별 크로스섹션 AUC 평균 + 상위 10% 예측 종목의 평균 순수익(비용 차감)·적중률.
실행: docker exec -w /app -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/exp_daytrade_label.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import numpy as np

PANEL = "/app/app/models/wf/panel_prod200_idn.npz"
COST = 0.0021
FOLDS = 5
DATES_PER_FOLD = 10
SEED = 42


def load_panel():
    d = np.load(PANEL, allow_pickle=True)
    X = np.asarray(d["X"], dtype=np.float32)
    dates = np.array([str(x) for x in d["dates"]])
    codes = np.array([str(x) for x in d["codes"]])
    price = np.asarray(d["price"], dtype=np.float64)
    return X, dates, codes, price


def make_labels_h5(dates, codes, price, horizon=5):
    uniq_dates = sorted(set(dates))
    idx = {dt: i for i, dt in enumerate(uniq_dates)}
    uniq_codes = sorted(set(codes))
    cidx = {c: i for i, c in enumerate(uniq_codes)}
    mat = np.full((len(uniq_dates), len(uniq_codes)), np.nan)
    for dt, c, p in zip(dates, codes, price):
        mat[idx[dt], cidx[c]] = p
    fwd = np.full_like(mat, np.nan)
    fwd[:-horizon] = mat[horizon:] / mat[:-horizon] - 1.0
    rel = fwd - np.nanmedian(fwd, axis=1, keepdims=True)
    y = np.full(len(dates), np.nan)
    for i, (dt, c) in enumerate(zip(dates, codes)):
        y[i] = rel[idx[dt], cidx[c]]
    return y


def load_daytrade_labels(dates, codes):
    import psycopg2

    lo = min(dates)
    hi = (datetime.strptime(max(dates), "%Y-%m-%d") + timedelta(days=14)).strftime("%Y-%m-%d")
    password = os.environ.get("POSTGRES_PASSWORD") or os.environ.get("PGPASSWORD", "")
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=password,
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT stock_code, trade_date, open_price, close_price FROM market_data "
        "WHERE stock_code = ANY(%s) AND trade_date >= %s AND trade_date <= %s "
        "ORDER BY stock_code, trade_date",
        (sorted(set(codes)), lo, hi),
    )
    per_code = {}
    for code, dt, o, c in cur.fetchall():
        per_code.setdefault(code, []).append((dt.isoformat(), o, c))
    conn.close()
    missing = sorted(set(codes) - set(per_code))
    if missing:
        print(f"no market_data rows for {len(missing)} codes: {missing[:10]}")
    y_raw = np.full(len(dates), np.nan)
    for code, rows in per_code.items():
        dts = np.array([r[0] for r in rows])
        opens = np.array([float(r[1]) if r[1] is not None else np.nan for r in rows])
        closes = np.array([float(r[2]) if r[2] is not None else np.nan for r in rows])
        mask = codes == code
        pos = np.searchsorted(dts, dates[mask], side="right")
        ok = pos < len(dts)
        if not ok.any():
            print(f"no next-day row for code {code}")
            continue
        idx = np.flatnonzero(mask)[ok]
        o = opens[pos[ok]]
        c = closes[pos[ok]]
        valid = np.isfinite(o) & np.isfinite(c) & (o > 0)
        y_raw[idx[valid]] = c[valid] / o[valid] - 1.0
    return y_raw


def binarize(y_raw, threshold):
    return np.where(np.isfinite(y_raw), y_raw > threshold, np.nan)


def auc_cs(df_dates, y, p):
    from sklearn.metrics import roc_auc_score

    aucs = []
    for dt in sorted(set(df_dates)):
        m = df_dates == dt
        yy = y[m]
        if len(np.unique(yy)) < 2:
            print(f"auc skip {dt}: single class")
            continue
        aucs.append(roc_auc_score(yy, p[m]))
    return aucs


def fit_predict(Xtr, ytr, Xte):
    import lightgbm as lgb
    import xgboost as xgb
    from catboost import CatBoostClassifier

    preds = []
    clf = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.05,
                            subsample=0.9, colsample_bytree=0.8, eval_metric="auc",
                            tree_method="hist", random_state=SEED)
    clf.fit(Xtr, ytr)
    preds.append(clf.predict_proba(Xte)[:, 1])
    clf = lgb.LGBMClassifier(n_estimators=300, num_leaves=31, learning_rate=0.05,
                             subsample=0.9, colsample_bytree=0.8, random_state=SEED, verbose=-1)
    clf.fit(Xtr, ytr)
    preds.append(clf.predict_proba(Xte)[:, 1])
    clf = CatBoostClassifier(iterations=300, depth=4, learning_rate=0.05,
                             random_seed=SEED, verbose=False)
    clf.fit(Xtr, ytr)
    preds.append(clf.predict_proba(Xte)[:, 1])
    return np.mean(preds, axis=0)


def run_experiment(tag, label_kind, X, dates, uniq_dates, purge, y_binary, net):
    from sklearn.metrics import roc_auc_score

    keep = np.isfinite(y_binary)
    Xk, yk, nk, dk = X[keep], y_binary[keep].astype(int), net[keep], dates[keep]
    tail = uniq_dates[-(FOLDS * DATES_PER_FOLD):]
    chunks = [tail[i * DATES_PER_FOLD:(i + 1) * DATES_PER_FOLD] for i in range(FOLDS)]
    print(f"[{tag}] rows={len(yk)} up_rate={float(yk.mean()):.4f} purge={purge}")
    fold_aucs, fold_nets, fold_hits, fold_rows = [], [], [], []
    pooled_y, pooled_p = [], []
    for i, chunk in enumerate(chunks):
        test_dates = set(chunk)
        first = min(test_dates)
        pos0 = uniq_dates.index(first)
        purge_dates = set(uniq_dates[max(0, pos0 - purge):pos0])
        train_mask = np.array([d < first and d not in purge_dates for d in dk])
        test_mask = np.array([d in test_dates for d in dk])
        p = fit_predict(Xk[train_mask], yk[train_mask], Xk[test_mask])
        aucs = auc_cs(dk[test_mask], yk[test_mask], p)
        fold_aucs.append(float(np.mean(aucs)))
        pooled_y.append(yk[test_mask])
        pooled_p.append(p)
        thr = np.quantile(p, 0.9)
        sel = p >= thr
        nsel = nk[test_mask][sel]
        fold_nets.append(float(np.mean(nsel)))
        fold_hits.append(float(np.mean(nsel > 0)))
        fold_rows.append(int(test_mask.sum()))
        print(f"  fold {i + 1}: train={int(train_mask.sum())} test={int(test_mask.sum())} "
              f"auc={fold_aucs[-1]:.4f} net_top10={fold_nets[-1]:.4f} hit_top10={fold_hits[-1]:.4f}")
    pooled = roc_auc_score(np.concatenate(pooled_y), np.concatenate(pooled_p))
    result = {
        "tag": tag,
        "label_kind": label_kind,
        "rows": int(len(yk)),
        "up_rate": round(float(yk.mean()), 4),
        "purge": purge,
        "folds": len(fold_aucs),
        "fold_test_rows": [int(r) for r in fold_rows],
        "fold_aucs": [round(a, 4) for a in fold_aucs],
        "auc_mean": round(float(np.mean(fold_aucs)), 4),
        "auc_std": round(float(np.std(fold_aucs)), 4),
        "auc_pooled": round(float(pooled), 4),
        "net_mean_top10": round(float(np.mean(fold_nets)), 4),
        "hit_rate_top10": round(float(np.mean(fold_hits)), 4),
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    X, dates, codes, price = load_panel()
    uniq_dates = sorted(set(dates))
    print(f"panel rows={len(dates)} dates={len(uniq_dates)} features={X.shape[1]}")

    y_raw_dt = load_daytrade_labels(dates, codes)
    run_experiment("daytrade_open_close", "daytrade_open_close", X, dates, uniq_dates, 1,
                   binarize(y_raw_dt, COST), y_raw_dt - COST)

    rel_h5 = make_labels_h5(dates, codes, price, horizon=5)
    run_experiment("baseline_h5_rel", "rel_h5", X, dates, uniq_dates, 5,
                   binarize(rel_h5, 0.0), rel_h5 - COST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
