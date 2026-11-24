#!/usr/bin/env python3
"""패널 기반 walk-forward 실험: 데이터 확대 + rel h=5 라벨의 효과 측정.

컨테이너에서 실행:
  docker exec -w /app stock_xgboost_ml python /app/scripts/exp_panel_wf.py --tag A
  docker exec -w /app stock_xgboost_ml python /app/scripts/exp_panel_wf.py --tag B --drop-dead

패널: /app/models/wf/panel_prod200_idn.npz (200종목 × 314거래일, 피처 235)
라벨: t+5 종가수익률에서 그날 표본 중앙값을 뺀 상대수익 > 0
평가: 연속 5폴드(폴드당 10거래일) 확장창, purge=5, 폴드별 크로스섹션 AUC 평균
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

PANEL = "/app/app/models/wf/panel_prod200_idn.npz"
OUT = "/app/reports"


def load_panel():
    d = np.load(PANEL, allow_pickle=True)
    X = np.asarray(d["X"], dtype=np.float32)
    names = [str(x) for x in d["feature_names"]]
    dates = np.array([str(x) for x in d["dates"]])
    codes = np.array([str(x) for x in d["codes"]])
    price = np.asarray(d["price"], dtype=np.float64)
    return X, names, dates, codes, price


def make_labels(dates, codes, price, horizon=5):
    uniq_dates = sorted(set(dates))
    idx = {dt: i for i, dt in enumerate(uniq_dates)}
    uniq_codes = sorted(set(codes))
    cidx = {c: i for i, c in enumerate(uniq_codes)}
    mat = np.full((len(uniq_dates), len(uniq_codes)), np.nan)
    for dt, c, p in zip(dates, codes, price):
        mat[idx[dt], cidx[c]] = p
    fwd = np.full_like(mat, np.nan)
    fwd[:-horizon] = mat[horizon:] / mat[:-horizon] - 1.0
    med = np.nanmedian(fwd, axis=1, keepdims=True)
    rel = fwd - med
    y = np.full(len(dates), np.nan)
    for i, (dt, c) in enumerate(zip(dates, codes)):
        y[i] = rel[idx[dt], cidx[c]]
    return y, uniq_dates


def auc_cs(df_dates, y, p):
    from sklearn.metrics import roc_auc_score
    aucs = []
    for dt in set(df_dates):
        m = df_dates == dt
        yy = y[m]
        if len(np.unique(yy)) < 2:
            continue
        aucs.append(roc_auc_score(yy, p[m]))
    return aucs


def fit_predict(Xtr, ytr, Xte, seed=42):
    import lightgbm as lgb
    import xgboost as xgb
    preds = []
    try:
        clf = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.05,
                                subsample=0.9, colsample_bytree=0.8, eval_metric="auc",
                                tree_method="hist", random_state=seed)
        clf.fit(Xtr, ytr)
        preds.append(clf.predict_proba(Xte)[:, 1])
    except Exception as exc:
        print("xgb fail", exc)
    try:
        clf = lgb.LGBMClassifier(n_estimators=300, num_leaves=31, learning_rate=0.05,
                                 subsample=0.9, colsample_bytree=0.8, random_state=seed, verbose=-1)
        clf.fit(Xtr, ytr)
        preds.append(clf.predict_proba(Xte)[:, 1])
    except Exception as exc:
        print("lgbm fail", exc)
    try:
        from catboost import CatBoostClassifier
        clf = CatBoostClassifier(iterations=300, depth=4, learning_rate=0.05,
                                 random_seed=seed, verbose=False)
        clf.fit(Xtr, ytr)
        preds.append(clf.predict_proba(Xte)[:, 1])
    except Exception as exc:
        print("cat fail", exc)
    return np.mean(preds, axis=0) if preds else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="A")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--dates-per-fold", type=int, default=10)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--drop-dead", action="store_true")
    ap.add_argument("--dead-json", default="/app/reports/dead_features.json")
    args = ap.parse_args()

    X, names, dates, codes, price = load_panel()
    y, uniq_dates = make_labels(dates, codes, price, args.horizon)
    keep = ~np.isnan(y)
    X, y, dates, codes = X[keep], y[keep], dates[keep], codes[keep]
    y = (y > 0).astype(int)

    drop_idx = []
    if args.drop_dead:
        dead = set(json.load(open(args.dead_json))) if os.path.exists(args.dead_json) else set()
        drop_idx = [i for i, n in enumerate(names) if n in dead]
        if drop_idx:
            X = np.delete(X, drop_idx, axis=1)
    print("rows", len(y), "features", X.shape[1], "up_rate", round(float(y.mean()), 4),
          "dropped", len(drop_idx))

    tail = uniq_dates[-(args.folds * args.dates_per_fold):]
    chunks = [tail[i * args.dates_per_fold:(i + 1) * args.dates_per_fold] for i in range(args.folds)]
    fold_aucs, pooled_y, pooled_p = [], [], []
    for i, chunk in enumerate(chunks):
        test_dates = set(chunk)
        first = min(test_dates)
        train_mask = np.array([d < first for d in dates])
        purge = set(uniq_dates[max(0, uniq_dates.index(first) - args.horizon):uniq_dates.index(first)])
        train_mask &= np.array([d not in purge for d in dates])
        test_mask = np.array([d in test_dates for d in dates])
        if train_mask.sum() < 500 or test_mask.sum() < 50:
            print("fold", i + 1, "skip (train", int(train_mask.sum()), "test", int(test_mask.sum()), ")")
            continue
        p = fit_predict(X[train_mask], y[train_mask], X[test_mask])
        if p is None:
            continue
        aucs = auc_cs(dates[test_mask], y[test_mask], p)
        fold_aucs.append(float(np.mean(aucs)))
        pooled_y.append(y[test_mask])
        pooled_p.append(p)
        print("fold", i + 1, "train", int(train_mask.sum()), "test", int(test_mask.sum()),
              "auc", round(fold_aucs[-1], 4))

    from sklearn.metrics import roc_auc_score
    pooled = roc_auc_score(np.concatenate(pooled_y), np.concatenate(pooled_p))
    result = {
        "tag": args.tag,
        "rows": int(len(y)),
        "features": int(X.shape[1]),
        "dropped_dead": len(drop_idx),
        "folds": len(fold_aucs),
        "fold_aucs": [round(a, 4) for a in fold_aucs],
        "fold_mean": round(float(np.mean(fold_aucs)), 4),
        "fold_std": round(float(np.std(fold_aucs)), 4),
        "auc_pooled": round(float(pooled), 4),
        "horizon": args.horizon,
        "label_kind": "rel",
    }
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "exp_panel_wf_{0}.json".format(args.tag))
    json.dump(result, open(path, "w"), ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
