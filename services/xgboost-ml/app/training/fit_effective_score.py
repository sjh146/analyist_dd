"""Offline fitting for ``USE_EFFECTIVE_SCORE`` activation (Bayesian + GP).

Fits and persists the artifacts that ``app.scoring.effective_score`` needs at
inference time:

1. ``calibrator.pkl``   — ``BayesianCalibrator`` (NumPyro NUTS): raw ensemble
   up-probability -> ``calibrated_probability`` (+ ``calibration_uncertainty``).
2. ``gp.pkl``           — ``GPUncertainty`` (sklearn GaussianProcessRegressor):
   low-dim momentum/volume/kalman features -> epistemic std ``sigma``.
3. ``bayes_factors.pkl`` — ``BayesFactorFeatures`` with a cached NumPyro
   posterior (fitted once offline on a reference close-price series), so the
   screener hot loop never runs MCMC.
4. ``meta.json``        — kappa, fit timestamp, calibration ECE (pre/post),
   GP R2, row counts, reference stock used for the Bayesian factor fit.

All heavy inference (NUTS MCMC, GPR fitting) happens HERE, offline. The
screener/backtester only load the artifacts and do cheap forward passes.

Usage (inside the xgboost-ml container, which has numpyro/jax pinned):

    docker compose run --rm xgboost-ml python -m app.training.fit_effective_score \
        --days 120 --stock-limit 120

The panel is built with the same ``FeaturePipeline.build_training_features``
path the production screener uses, and the labels mirror ``Trainer._create_labels``
(1-day forward close direction), so the calibration reflects the real
inference-time probability distribution.
"""

import argparse
import json
import logging
import os
import pickle
import tempfile
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.calibration.bayesian_calibration import BayesianCalibrator
from app.feature_engine.bayes_factor_features import BayesFactorFeatures
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.uncertainty.gp_uncertainty import GPUncertainty, LOW_DIM_FEATURES

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = "app/models/effective_score"
DEFAULT_KAPPA = 0.3
DEFAULT_BAYES_REF_STOCK = "005930"  # most liquid reference series for the state-space fit


def expected_calibration_error(y_true, probs, n_bins: int = 10) -> float:
    """Binned expected calibration error over [0, 1]."""
    y = np.asarray(y_true, dtype=np.float64)
    p = np.asarray(probs, dtype=np.float64)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.digitize(p, bins[1:-1])
    ece = 0.0
    for i in range(n_bins):
        mask = idx == i
        if mask.sum() == 0:
            continue
        ece += abs(p[mask].mean() - y[mask].mean()) * (mask.sum() / len(p))
    return float(ece)


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


def _champion_matrix(df: pd.DataFrame, feature_names: Sequence[str]) -> np.ndarray:
    """Feature matrix in champion order; missing columns filled with 0.0
    (identical to the screener's ``features.get(f, 0.0)`` inference behavior)."""
    X = np.zeros((len(df), len(feature_names)), dtype=np.float32)
    for j, name in enumerate(feature_names):
        if name in df.columns:
            col = df[name].to_numpy(dtype=np.float64)
            X[:, j] = np.nan_to_num(col, nan=0.0, posinf=0.0, neginf=0.0)
    return X


def fit_effective_score_from_df(
    df: pd.DataFrame,
    prob_fn: Callable[[np.ndarray], np.ndarray],
    champion_feature_names: Sequence[str],
    out_dir: str,
    kappa: float = DEFAULT_KAPPA,
    cal_fit_frac: float = 0.8,
    num_warmup: int = 200,
    num_samples: int = 200,
    bayes_ref_close: Optional[Sequence[float]] = None,
    bayes_ref_stock: str = DEFAULT_BAYES_REF_STOCK,
    gp_max_rows: int = 3000,
    seed: int = 0,
) -> Dict:
    """Core fitting routine (DB-free; unit-testable on a synthetic DataFrame).

    Parameters
    ----------
    df : training panel from ``FeaturePipeline.build_training_features`` (must
        contain ``date``, ``stock_code``, ``price`` and the low-dim GP columns).
    prob_fn : callable mapping an (n, len(champion_feature_names)) matrix to
        ensemble up-probabilities in [0, 1].
    champion_feature_names : the exact feature order the ensemble expects.
    out_dir : directory where calibrator.pkl / gp.pkl / bayes_factors.pkl /
        meta.json are written (created if missing).
    bayes_ref_close : close-price series used for the ONE offline
        ``BayesFactorFeatures`` fit. If None, the longest per-stock ``price``
        series in ``df`` is used.

    Returns ``meta`` dict with calibration/GP/fit summary.
    """
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.RandomState(seed)

    df = df.copy()
    if "date" not in df.columns:
        raise ValueError("df must contain a 'date' column")
    df = df.sort_values("date").reset_index(drop=True)

    # ---- 1. Labels + probabilities (production inference path) ----
    y = _create_labels(df)
    X_champion = _champion_matrix(df, champion_feature_names)
    probs = np.asarray(prob_fn(X_champion), dtype=np.float64).ravel()
    probs = np.clip(probs, 1e-6, 1 - 1e-6)

    # ---- 2. Bayesian calibration with a chronological split ----
    split = int(len(df) * cal_fit_frac)
    cal = BayesianCalibrator(
        num_warmup=num_warmup, num_samples=num_samples, seed=seed
    )
    cal.fit(probs[:split], y[:split])
    ece_pre = expected_calibration_error(y[split:], probs[split:])
    cal_probs_eval = np.asarray(
        [cal.calibrate(p)["calibrated_probability"] for p in probs[split:]],
        dtype=np.float64,
    )
    ece_post = expected_calibration_error(y[split:], cal_probs_eval)

    # ---- 3. GP epistemic uncertainty (low-dim features -> 5d fwd return) ----
    gp_rows = []
    for f in LOW_DIM_FEATURES:
        col = np.nan_to_num(
            df[f].to_numpy(dtype=np.float64) if f in df.columns else np.zeros(len(df)),
            nan=0.0, posinf=0.0, neginf=0.0,
        )
        gp_rows.append(col)
    X_gp = np.column_stack(gp_rows)
    # Future 5-day return from close prices (shift per stock).
    y_5d = df.groupby("stock_code")["price"].transform(
        lambda s: s.shift(-5) / s - 1.0
    ).to_numpy(dtype=np.float64)
    valid = np.isfinite(y_5d) & np.isfinite(X_gp).all(axis=1)
    X_gp, y_5d = X_gp[valid], y_5d[valid]
    if len(X_gp) > gp_max_rows:
        pick = rng.choice(len(X_gp), size=gp_max_rows, replace=False)
        X_gp, y_5d = X_gp[pick], y_5d[pick]

    gp = GPUncertainty(feature_names=LOW_DIM_FEATURES, random_state=seed)
    gp.fit(X_gp, y_5d)
    y_pred = gp.model.predict(X_gp)
    ss_res = float(np.sum((y_5d - y_pred) ** 2))
    ss_tot = float(np.sum((y_5d - y_5d.mean()) ** 2))
    gp_r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # ---- 4. Bayesian factor features (ONE offline state-space fit) ----
    if bayes_ref_close is None or len(bayes_ref_close) < 5:
        # Longest per-stock close series available in the panel.
        longest = (
            df.groupby("stock_code")["price"]
            .count().idxmax()
        )
        bayes_ref_close = df.loc[df["stock_code"] == longest, "price"].tolist()
        bayes_ref_stock = f"{bayes_ref_stock}->{longest}"
    bf = BayesFactorFeatures(num_warmup=num_warmup, num_samples=num_samples, seed=seed)
    bf.fit(np.asarray(bayes_ref_close, dtype=np.float64))
    # Convert jax arrays -> numpy so pickle works across container runs.
    if bf._posterior is not None:
        bf._posterior = {k: np.asarray(v) for k, v in bf._posterior.items()}

    # ---- 5. Persist ----
    with open(os.path.join(out_dir, "calibrator.pkl"), "wb") as f:
        pickle.dump(cal, f)
    with open(os.path.join(out_dir, "gp.pkl"), "wb") as f:
        pickle.dump(gp, f)
    with open(os.path.join(out_dir, "bayes_factors.pkl"), "wb") as f:
        pickle.dump(bf, f)

    meta = {
        "kappa": float(kappa),
        "fitted_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": int(len(df)),
        "n_cal_fit": int(split),
        "n_cal_eval": int(len(df) - split),
        "ece_pre": round(ece_pre, 5),
        "ece_post": round(ece_post, 5),
        "gp_n_rows": int(len(X_gp)),
        "gp_r2": round(gp_r2, 5),
        "gp_features": list(LOW_DIM_FEATURES),
        "champion_features_count": int(len(champion_feature_names)),
        "bayes_ref_stock": bayes_ref_stock,
        "bayes_mcmc": {"warmup": num_warmup, "samples": num_samples},
        "bayes_posterior_cached": bf._posterior is not None,
        "calibrator_rhat": {k: round(v, 4) for k, v in cal.rhat.items()},
    }
    tmp_path = os.path.join(out_dir, f"meta.json.{os.getpid()}.tmp")
    with open(tmp_path, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, os.path.join(out_dir, "meta.json"))

    logger.info(
        "fit complete: n=%d ece %.4f->%.4f gp_r2=%.4f bayes_cached=%s -> %s",
        len(df), ece_pre, ece_post, gp_r2, bf._posterior is not None, out_dir,
    )
    return meta


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
    cur = pg.cursor()
    cur.execute(
        """
        SELECT stock_code FROM market_data
        GROUP BY stock_code
        ORDER BY MAX(trade_date) DESC, COUNT(*) DESC
        LIMIT %s
        """,
        (limit,),
    )
    rows = cur.fetchall()
    cur.close()
    return [r[0] for r in rows]


def _load_ref_close(pg, stock_code: str, limit: int = 250) -> List[float]:
    cur = pg.cursor()
    cur.execute(
        """
        SELECT close_price FROM market_data
        WHERE stock_code = %s
        ORDER BY trade_date DESC LIMIT %s
        """,
        (stock_code, limit),
    )
    rows = cur.fetchall()
    cur.close()
    return [float(r[0]) for r in reversed(rows)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline fit for USE_EFFECTIVE_SCORE")
    ap.add_argument("--days", type=int, default=120, help="training window (days)")
    ap.add_argument("--stock-limit", type=int, default=120, help="max stocks (most recent data first)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--model-dir", default="app/models/champion")
    ap.add_argument("--kappa", type=float, default=DEFAULT_KAPPA)
    ap.add_argument("--bayes-ref-stock", default=DEFAULT_BAYES_REF_STOCK)
    ap.add_argument("--num-warmup", type=int, default=200)
    ap.add_argument("--num-samples", type=int, default=200)
    ap.add_argument("--gp-max-rows", type=int, default=3000)
    ap.add_argument("--mcmc-light", action="store_true",
                    help="tiny MCMC (tests/smoke only)")
    args = ap.parse_args()

    if args.mcmc_light:
        args.num_warmup, args.num_samples = 5, 10

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # During training-panel building the BayesFactorFeatures posterior is not
    # fit yet (it is fit at the end of this script) — silence the expected
    # per-stock "compute before fit" warnings, they are not actionable here.
    logging.getLogger("app.feature_engine.bayes_factor_features").setLevel(logging.ERROR)

    pg = _pg_connect()
    try:
        stocks = _select_stocks(pg, args.stock_limit)
        logger.info("selected %d stocks (most recent data first)", len(stocks))
        pipeline = FeaturePipeline(pg_conn=pg)
        end = datetime.now()
        start = end - timedelta(days=args.days)
        df = pipeline.build_training_features(
            stocks, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
        if df is None or len(df) < 100:
            logger.error("insufficient training rows: %s", 0 if df is None else len(df))
            return

        ensemble = EnsembleModel(model_dir=args.model_dir)
        ensemble.load(args.model_dir)
        with open(os.path.join(args.model_dir, "feature_names.json")) as f:
            champion_features = json.load(f)
        logger.info("champion features: %d, panel rows: %d", len(champion_features), len(df))

        ref_close = _load_ref_close(pg, args.bayes_ref_stock)

        meta = fit_effective_score_from_df(
            df=df,
            prob_fn=lambda X: ensemble.predict(X),
            champion_feature_names=champion_features,
            out_dir=args.out_dir,
            kappa=args.kappa,
            num_warmup=args.num_warmup,
            num_samples=args.num_samples,
            bayes_ref_close=ref_close,
            bayes_ref_stock=args.bayes_ref_stock,
            gp_max_rows=args.gp_max_rows,
        )
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"ARTIFACTS -> {os.path.abspath(args.out_dir)}")
    finally:
        pg.close()


if __name__ == "__main__":
    main()
