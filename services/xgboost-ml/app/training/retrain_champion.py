"""Deterministic champion retrain with an explicit train/inference feature contract.

WHY: the previous champion was trained by the strategy-experiment loop, which
trained each model on a DIFFERENT feature subset without persisting the column
order (catboost=23, lightgbm=62 positional columns, no names) while
feature_names.json was a separately sorted list. Inference fed features in json
order -> columns misaligned + ~20 features always 0.0 -> the ensemble collapsed
to a near-constant all-DOWN prediction (up:0/down:1668, hit Aug 2026).

FIX: train ALL three models on the SAME canonical feature matrix whose column
order is ``FeaturePipeline.get_feature_names()`` (a fixed sorted list) and write
``feature_names.json`` in that EXACT order. The screener builds its vector from
that same json with ``features.get(f, 0.0)``, so training and inference now use
identical positions. CatBoost additionally receives the real feature names so
its model metadata is debuggable.

Usage (in the xgboost-ml container):
    docker compose run --rm xgboost-ml python -m app.training.retrain_champion \
        --days 120 --stock-limit 200
"""

import argparse
import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.catboost_model import CatBoostModel
from app.models.lightgbm_model import LightGBMModel
from app.models.xgboost_model import XGBoostModel

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = "app/models/champion"


def _create_labels(df: pd.DataFrame) -> np.ndarray:
    """1-day forward close direction per stock (mirrors Trainer._create_labels)."""
    labels = np.zeros(len(df), dtype=int)
    if "stock_code" not in df.columns or "price" not in df.columns:
        return labels
    for code in df["stock_code"].unique():
        mask = df["stock_code"] == code
        idx = df[mask].index
        prices = df.loc[idx, "price"].values.astype(np.float64)
        if len(prices) >= 2:
            next_up = prices[1:] > prices[:-1]
            vals = np.zeros(len(prices), dtype=int)
            vals[:-1] = next_up.astype(int)
            labels[idx] = vals
    return labels


def _add_cross_sectional_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Batch-level cross-sectional ranks, replicating
    FeaturePipeline.compute_cross_sectional_ranks / Trainer (trainer.py:107-118)."""
    rank_cols = [
        "return_5d", "return_20d", "volatility_20d",
        "volume_ratio_5", "ma_position_5", "volume_ratio_20",
    ]
    date_col = "date" if "date" in df.columns else ("trade_date" if "trade_date" in df.columns else None)
    out = df.copy()
    if date_col is None:
        return out
    for col in rank_cols:
        if col in out.columns:
            out[f"rank_{col}"] = out.groupby(date_col)[col].rank(pct=True)
    return out


def _pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _select_stocks(pg, limit: int) -> List[str]:
    """재학습 유니버스 — ETF/ETN 제외 + 최근 데이터 우선 (universe.py 참조)."""
    from app.training.universe import select_training_universe

    return select_training_universe(pg, limit=limit, min_days=30, seed=0)


def retrain_champion(
    df: pd.DataFrame,
    out_dir: str,
    val_frac: float = 0.2,
    n_estimators: int = 500,
    seed: int = 0,
) -> dict:
    """Core retrain (DB-free, testable): train 3 models on ONE canonical matrix.

    Returns a summary dict with per-model val AUC, ensemble AUC, feature count.
    """
    from app.feature_engine.feature_pipeline import FeaturePipeline as _FP

    os.makedirs(out_dir, exist_ok=True)

    df = _add_cross_sectional_ranks(df)
    df = df.sort_values("date").reset_index(drop=True)
    y = _create_labels(df)

    # Canonical contract: the FULL sorted feature list, same order the screener
    # builds. Missing columns are 0-filled on BOTH sides (train + inference).
    canonical: List[str] = _FP().get_feature_names()
    X = np.zeros((len(df), len(canonical)), dtype=np.float32)
    for j, name in enumerate(canonical):
        if name in df.columns:
            col = df[name].to_numpy(dtype=np.float64)
            X[:, j] = np.nan_to_num(col, nan=0.0, posinf=0.0, neginf=0.0)

    # Chronological split (walk-forward style: train on past, val on future).
    split = int(len(df) * (1.0 - val_frac))
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    up_rate = float(y.mean())

    models = [
        ("xgboost", XGBoostModel()),
        ("lightgbm", LightGBMModel()),
        ("catboost", CatBoostModel()),
    ]
    weights: dict = {}
    aucs: dict = {}
    saved: List[str] = []
    for name, model in models:
        try:
            metrics = model.train(X_train, y_train, X_val, y_val)
            auc = 0.5
            if X_val is not None and len(X_val) > 10:
                try:
                    auc = roc_auc_score(y_val, model.predict(X_val))
                except Exception:
                    auc = 0.5
            aucs[name] = round(float(auc), 4)
            weights[name] = max(auc - 0.5, 0.01)
            path = os.path.join(out_dir, f"{name}_model.pkl")
            model.save(path)
            saved.append(path)
            logger.info("%s val AUC=%.4f -> saved %s", name, auc, path)
        except Exception as e:
            logger.warning("%s training failed: %s", name, e)
            aucs[name] = None

    if not saved:
        raise RuntimeError("no model trained")

    # Ensemble AUC (weighted soft-vote on val).
    ens_auc = 0.5
    if X_val is not None and len(X_val) > 10:
        probs = np.zeros(len(X_val))
        tw = 0.0
        for name, model in models:
            w = weights.get(name)
            if w is None:
                continue
            probs += w * model.predict(X_val)
            tw += w
        if tw > 0:
            try:
                ens_auc = roc_auc_score(y_val, probs / tw)
            except Exception:
                ens_auc = 0.5

    # Persist the contract + metadata.
    with open(os.path.join(out_dir, "feature_names.json"), "w") as f:
        json.dump(canonical, f)
    with open(os.path.join(out_dir, "auc.txt"), "w") as f:
        f.write(f"{ens_auc:.6f}\n")
    meta = {
        "retrained_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": int(len(df)),
        "n_train": int(split),
        "n_val": int(len(df) - split),
        "up_rate": round(up_rate, 4),
        "n_features": int(len(canonical)),
        "model_aucs": aucs,
        "ensemble_auc": round(float(ens_auc), 4),
        "val_frac": val_frac,
        "seed": seed,
    }
    meta_path = os.path.join(out_dir, f"training-result-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info(
        "champion retrain done: n=%d up_rate=%.3f ensemble_auc=%.4f features=%d -> %s",
        len(df), up_rate, ens_auc, len(canonical), out_dir,
    )
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="Deterministic champion retrain")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--stock-limit", type=int, default=200)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--n-estimators", type=int, default=500)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("app.feature_engine.bayes_factor_features").setLevel(logging.ERROR)

    pg = _pg_connect()
    try:
        stocks = _select_stocks(pg, args.stock_limit)
        logger.info("selected %d stocks", len(stocks))
        pipeline = FeaturePipeline(pg_conn=pg)
        end = datetime.now()
        start = end - timedelta(days=args.days)
        df = pipeline.build_training_features(
            stocks, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
        if df is None or len(df) < 500:
            logger.error("insufficient panel rows: %s", 0 if df is None else len(df))
            return
        meta = retrain_champion(df, out_dir=args.out_dir,
                                val_frac=args.val_frac, n_estimators=args.n_estimators)
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"CHAMPION -> {os.path.abspath(args.out_dir)}")
    finally:
        pg.close()


if __name__ == "__main__":
    main()
