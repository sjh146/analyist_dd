#!/usr/bin/env python
"""ML AUC 실험 하네스 — 피처 캐시 + 멀티시드 + 워크포워드 + 챔피언 계약 저장.

피처 행렬(200종목×N일)을 1회 빌드해 디스크에 캐시(panel.pkl)하고, 이후
학습/평가는 캐시를 재사용한다. 캐시 키 = 종목수 + 기간 + 마지막 거래일.
OOS 측정은 별도 스크립트(phase_4_backtest.py)로 수행한다.

서브커맨드:
  build       피처 행렬 빌드 + 캐시
  multiseed   멀티시드 평균 val AUC(±std) 측정 (+ --save-dir 로 챔피언 계약 저장)
  walkforward 워크포워드 평균 AUC 측정
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")

from sklearn.metrics import roc_auc_score
import psycopg2

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.training.retrain_champion import _create_labels, _add_cross_sectional_ranks
from app.training.universe import select_training_universe
from app.models.xgboost_model import XGBoostModel
from app.models.lightgbm_model import LightGBMModel
from app.models.catboost_model import CatBoostModel

DEFAULT_CACHE_DIR = "app/models/exp_panel"


def pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def last_trade_date(pg):
    cur = pg.cursor()
    cur.execute("SELECT MAX(trade_date)::text FROM market_data")
    d = cur.fetchone()[0]
    cur.close()
    return d


def cache_key(n_stocks, days, last_dt):
    return f"s{n_stocks}_d{days}_t{last_dt}"


def build_panel(pg, n_stocks, days, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    last_dt = last_trade_date(pg)
    key = cache_key(n_stocks, days, last_dt)
    meta_path = os.path.join(cache_dir, "meta.json")
    panel_path = os.path.join(cache_dir, "panel.pkl")
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path))
        if meta.get("cache_key") == key and os.path.exists(panel_path):
            print(f"[cache] hit {key} -> {panel_path}")
            return panel_path
    stocks = select_training_universe(pg, limit=n_stocks, min_days=30, seed=0)
    print(f"[build] universe {len(stocks)} stocks (limit {n_stocks})", flush=True)
    pipeline = FeaturePipeline(pg_conn=pg)
    end = datetime.now()
    start = end - timedelta(days=days)
    df = pipeline.build_training_features(
        stocks, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    if df is None or len(df) < 500:
        raise RuntimeError(f"insufficient panel rows: {0 if df is None else len(df)}")
    df = _add_cross_sectional_ranks(df)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_pickle(panel_path)
    json.dump(
        {
            "cache_key": key,
            "n_stocks": len(stocks),
            "days": days,
            "last_trade_date": last_dt,
            "n_rows": int(len(df)),
            "n_cols": int(len(df.columns)),
            "built_at": datetime.now().isoformat(timespec="seconds"),
        },
        open(meta_path, "w"),
        indent=2,
    )
    print(f"[build] saved {panel_path} rows={len(df)} cols={len(df.columns)}", flush=True)
    return panel_path


def canonical_matrix(df):
    canonical = FeaturePipeline().get_feature_names()
    X = np.zeros((len(df), len(canonical)), dtype=np.float32)
    for j, name in enumerate(canonical):
        if name in df.columns:
            col = df[name].to_numpy(dtype=np.float64)
            X[:, j] = np.nan_to_num(col, nan=0.0, posinf=0.0, neginf=0.0)
    y = _create_labels(df)
    return X, y, canonical


def make_models(cfg, seed):
    num_leaves = min(2 ** cfg["max_depth"], 255)
    xgb = XGBoostModel(n_estimators=cfg["n_estimators"], random_state=seed)
    lgb = LightGBMModel(n_estimators=cfg["n_estimators"], random_state=seed)
    cat = CatBoostModel(iterations=min(cfg["n_estimators"], 60), random_state=seed)

    for m in (xgb, lgb):
        m.params["max_depth"] = cfg["max_depth"]
        m.params["learning_rate"] = cfg["learning_rate"]
        m.params["colsample_bytree"] = cfg["colsample"]
        m.params["scale_pos_weight"] = cfg["scale_pos_weight"]
        m.params["reg_lambda"] = cfg["reg_lambda"]
    xgb.params["min_child_weight"] = cfg["min_child"]
    xgb.params["subsample"] = cfg["subsample"]
    lgb.params["min_child_samples"] = cfg["min_child"]
    lgb.params["subsample"] = cfg["subsample"]
    lgb.params["num_leaves"] = num_leaves

    cat.params["depth"] = cfg["max_depth"]
    cat.params["learning_rate"] = cfg["learning_rate"]
    cat.params["l2_leaf_reg"] = cfg["reg_lambda"]
    return [("xgboost", xgb), ("lightgbm", lgb), ("catboost", cat)]


def _fit_eval(models, Xtr, ytr, Xva, yva):
    aucs = {}
    for name, model in models:
        model.train(Xtr, ytr, Xva, yva)
        p = model.predict(Xva)
        try:
            a = roc_auc_score(yva, p)
        except Exception:
            a = 0.5
        aucs[name] = a
    wsum = 0.0
    probs = np.zeros(len(yva))
    for name, model in models:
        w = max(aucs[name] - 0.5, 0.01)
        probs += w * model.predict(Xva)
        wsum += w
    ens = roc_auc_score(yva, probs / wsum) if wsum > 0 else 0.5
    return aucs, ens


def fmt(arr):
    arr = np.asarray(arr, dtype=float)
    return f"{arr.mean():.4f}±{arr.std():.4f}"


def save_champion_contract(models, aucs, ens_auc, canonical, out_dir, n_rows, seed, cfg):
    os.makedirs(out_dir, exist_ok=True)
    for name, model in models:
        model.save(os.path.join(out_dir, f"{name}_model.pkl"))
    with open(os.path.join(out_dir, "feature_names.json"), "w") as f:
        json.dump(canonical, f)
    with open(os.path.join(out_dir, "auc.txt"), "w") as f:
        f.write(f"{ens_auc:.6f}\n")
    meta = {
        "retrained_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": int(n_rows),
        "n_features": int(len(canonical)),
        "model_aucs": {k: round(v, 4) for k, v in aucs.items()},
        "ensemble_auc": round(float(ens_auc), 4),
        "val_frac": 0.2,
        "seed": seed,
        "cfg": cfg,
    }
    mp = os.path.join(out_dir, f"training-result-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(mp, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[save] champion contract -> {out_dir} (ens auc {ens_auc:.4f})")


def cmd_build(args):
    pg = pg_connect()
    try:
        build_panel(pg, args.stock_limit, args.days, args.cache_dir)
    finally:
        pg.close()


def cmd_multiseed(args):
    df = pd.read_pickle(os.path.join(args.cache_dir, "panel.pkl"))
    X, y, canonical = canonical_matrix(df)
    split = int(len(df) * (1.0 - args.val_frac))
    Xtr, Xva, ytr, yva = X[:split], X[split:], y[:split], y[split:]
    seeds = [int(s) for s in args.seeds.split(",")]
    per_model = {"xgboost": [], "lightgbm": [], "catboost": []}
    ens_list = []
    cfg = {
        "n_estimators": args.n_estimators,
        "max_depth": args.max_depth,
        "learning_rate": args.learning_rate,
        "min_child": args.min_child,
        "reg_lambda": args.reg_lambda,
        "colsample": args.colsample,
        "subsample": args.subsample,
        "scale_pos_weight": args.scale_pos_weight,
    }
    t0 = time.time()
    for s in seeds:
        models = make_models(cfg, s)
        aucs, ens = _fit_eval(models, Xtr, ytr, Xva, yva)
        for name in per_model:
            per_model[name].append(aucs[name])
        ens_list.append(ens)
        print(f"  seed {s}: xgb={aucs['xgboost']:.4f} lgb={aucs['lightgbm']:.4f} "
              f"cat={aucs['catboost']:.4f} ens={ens:.4f}", flush=True)
    print(f"[multiseed] n={len(ytr)}+{len(yva)} up_rate={y.mean():.3f} "
          f"seeds={seeds} ({time.time()-t0:.1f}s)")
    print(f"[multiseed] xgboost  = {fmt(per_model['xgboost'])}")
    print(f"[multiseed] lightgbm = {fmt(per_model['lightgbm'])}")
    print(f"[multiseed] catboost = {fmt(per_model['catboost'])}")
    print(f"[multiseed] ENSEMBLE = {fmt(ens_list)}")

    if args.save_dir:
        models = make_models(cfg, args.final_seed)
        aucs, ens = _fit_eval(models, Xtr, ytr, Xva, yva)
        save_champion_contract(models, aucs, ens, canonical, args.save_dir,
                               int(len(df)), args.final_seed, cfg)


def cmd_walkforward(args):
    df = pd.read_pickle(os.path.join(args.cache_dir, "panel.pkl"))
    X, y, canonical = canonical_matrix(df)
    dates = df["date"].astype(str).values
    unique = sorted(set(dates))
    n = len(unique)
    wd, sd, pd_ = args.window_days, args.step_days, args.purge_days
    cfg = vars(args).copy()
    wins = []
    max_start = n - wd - pd_ - sd
    for start_idx in range(0, max_start + 1, sd):
        train_dates = set(unique[start_idx:start_idx + wd])
        test_dates = set(unique[start_idx + wd + pd_:start_idx + wd + pd_ + sd])
        tr = np.array([d in train_dates for d in dates])
        te = np.array([d in test_dates for d in dates])
        if tr.sum() < 50 or te.sum() < 10:
            continue
        Xtr, ytr = X[tr], y[tr]
        Xte, yte = X[te], y[te]
        if ytr.sum() == 0 or ytr.sum() == len(ytr):
            continue
        model = XGBoostModel(n_estimators=cfg["n_estimators"],
                             random_state=cfg["final_seed"])
        model.params["max_depth"] = cfg["max_depth"]
        model.params["learning_rate"] = cfg["learning_rate"]
        model.train(Xtr, ytr)
        p = model.predict(Xte)
        try:
            a = roc_auc_score(yte, p)
        except Exception:
            a = 0.5
        wins.append(a)
        print(f"  wf window start={unique[start_idx]} train={tr.sum()} test={te.sum()} auc={a:.4f}",
              flush=True)
    if not wins:
        print("[walkforward] no windows completed")
        return
    arr = np.asarray(wins)
    print(f"[walkforward] n_windows={len(wins)} mean_auc={arr.mean():.4f}±{arr.std():.4f}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("--stock-limit", type=int, default=200)
    b.add_argument("--days", type=int, default=120)
    b.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    b.set_defaults(func=cmd_build)

    m = sub.add_parser("multiseed")
    m.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    m.add_argument("--seeds", default="0,1,2,3,4")
    m.add_argument("--val-frac", type=float, default=0.2)
    m.add_argument("--n-estimators", type=int, default=500)
    m.add_argument("--max-depth", type=int, default=8)
    m.add_argument("--learning-rate", type=float, default=0.05)
    m.add_argument("--min-child", type=int, default=3)
    m.add_argument("--reg-lambda", type=float, default=1.0)
    m.add_argument("--colsample", type=float, default=0.7)
    m.add_argument("--subsample", type=float, default=0.7)
    m.add_argument("--scale-pos-weight", type=float, default=1.4)
    m.add_argument("--save-dir", default=None)
    m.add_argument("--final-seed", type=int, default=42)
    m.set_defaults(func=cmd_multiseed)

    w = sub.add_parser("walkforward")
    w.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    w.add_argument("--window-days", type=int, default=60)
    w.add_argument("--step-days", type=int, default=20)
    w.add_argument("--purge-days", type=int, default=5)
    w.add_argument("--n-estimators", type=int, default=300)
    w.add_argument("--max-depth", type=int, default=5)
    w.add_argument("--learning-rate", type=float, default=0.05)
    w.add_argument("--final-seed", type=int, default=42)
    w.set_defaults(func=cmd_walkforward)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
