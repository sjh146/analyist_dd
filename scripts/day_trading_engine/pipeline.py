"""Day-trading screener pipeline orchestration.

Wires a :class:`MarketDataProvider` + kalman features + optional champion-model
probabilities into a ranked candidate list.  Kept DB- and model-lib-agnostic so
it can be exercised with fixtures and a fake predictor in tests.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

from .providers import MarketDataProvider, StockInfo
from .scoring import (compute_kalman_features, filter_candidates,
                      rank_candidates, score_candidates)

logger = logging.getLogger("day_trading_engine.pipeline")

OUTPUT_COLUMNS = [
    "rank", "stock_code", "stock_name", "sector", "signal_date", "close_price",
    "score", "kalman_trend", "kalman_slope", "noise_resid_std",
    "volume_surge", "volatility_ann", "model_prob", "reason",
]


class ChampionPredictor:
    """Loads the trained ensemble and predicts per-stock up-probability.

    The feature vector is built in the order mandated by
    ``models/champion/feature_names.json`` (§9 of the plan).  When the model
    libraries (xgboost/lightgbm/catboost) are unavailable — or the artifacts
    are missing — the predictor reports :meth:`available` False and prediction
    returns ``None``, letting the screener degrade to kalman/volume/vol scoring.
    """

    def __init__(self, model_dir: str | None = None):
        self._model_dir = model_dir or self._default_model_dir()
        self._ensemble = None
        self._feature_names = self._load_feature_names()
        self._available = self._feature_names is not None and os.path.isdir(self._model_dir)
        if self._available:
            self._try_load()

    @staticmethod
    def _default_model_dir() -> str:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "services", "xgboost-ml", "app", "models", "champion",
        )

    def _load_feature_names(self):
        path = os.path.join(self._model_dir, "feature_names.json")
        if not os.path.exists(path):
            logger.warning("feature_names.json not found — model features unavailable")
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:  # pragma: no cover
            logger.warning(f"feature_names.json load failed: {e}")
            return None

    def _try_load(self):  # pragma: no cover - requires native libs on host
        try:
            sys.path.insert(0, os.path.join(self._model_dir, "..", ".."))
            from app.models.ensemble_model import EnsembleModel
            ensemble = EnsembleModel(model_dir=self._model_dir)
            ensemble.load(self._model_dir)
            if not ensemble._is_trained:
                logger.warning("champion ensemble not trained — model unavailable")
                self._available = False
                return
            self._ensemble = ensemble
        except Exception as e:
            logger.warning(f"champion model load failed ({type(e).__name__}): {e}")
            self._available = False
            self._ensemble = None

    @property
    def available(self) -> bool:
        return self._available and self._ensemble is not None

    def predict(self, df: pd.DataFrame) -> Optional[pd.Series]:
        """Map denoised feature rows → up-probability Series (idx=stock_code)."""
        if not self.available or self._feature_names is None:
            return None
        fnames = self._feature_names
        probs = {}
        for _, r in df.iterrows():
            stock_code = r["stock_code"]
            fv = np.array(
                [self._feature_value(r, f, stock_code) for f in fnames],
                dtype=np.float32,
            )
            fv = np.nan_to_num(fv, nan=0.0)
            try:
                p = float(self._ensemble.predict(fv.reshape(1, -1))[0])
            except Exception as e:  # pragma: no cover
                logger.debug(f"predict failed for {stock_code}: {e}")
                continue
            probs[stock_code] = p
        if not probs:
            return None
        return pd.Series(probs, dtype=float)

    def _feature_value(self, row, feature: str, stock_code: str) -> float:
        """Best-effort map from denoised row → feature_names.json column.

        Real deploy passes a full feature pipeline (swing_screener path).  Here
        we surface the kalman/volume/vol fields we actually compute; everything
        else is 0.0 (model gracefully degrades to a kalman-driven prior).
        """
        # Known denoised/technical aliases we can provide from the screener row.
        alias = {
            "kalman_momentum_1d": "kalman_trend",
            "kalman_momentum_5d": "kalman_trend",
            "kalman_volatility": "volatility_ann",
            "volume_ratio_5": "volume_surge",
        }
        if feature in row.index:
            v = row[feature]
            return float(v) if pd.notna(v) else 0.0
        if feature in alias and alias[feature] in row.index:
            v = row[alias[feature]]
            return float(v) if pd.notna(v) else 0.0
        return 0.0


def run_screener(provider: MarketDataProvider,
                 top_n: int = 20,
                 lookback: int = 20,
                 min_history: int = 20,
                 min_trading_value: float = 300_000_000,
                 min_price: float = 1000,
                 date_str: str | None = None,
                 predictor: Optional[ChampionPredictor] = None) -> pd.DataFrame:
    """Full pipeline: universe → lookback → kalman → (model) → score → rank.

    Returns a ranked DataFrame in :data:`OUTPUT_COLUMNS` order (may be empty).
    """
    signal_date = provider.resolve_signal_date(date_str)
    logger.info(f"시그널 기준일: {signal_date}")

    universe = provider.get_universe(min_history=min_history)
    universe_codes = {s.stock_code for s in universe}
    logger.info(f"유니버스: {len(universe)} 종목")

    meta = {s.stock_code: (s.stock_name, s.sector) for s in universe}

    lookback_df = provider.load_lookback(signal_date, lookback=lookback)
    lookback_df["stock_code"] = lookback_df["stock_code"].astype(str)
    lookback_df = lookback_df[lookback_df["stock_code"].isin(universe_codes)]
    logger.info(f"룩백 로드: {len(lookback_df)} 행 / {lookback_df['stock_code'].nunique()} 종목")

    kf = compute_kalman_features(lookback_df)
    kf["stock_name"] = kf["stock_code"].map(lambda c: meta.get(c, ("", "Unknown"))[0])
    kf["sector"] = kf["stock_code"].map(lambda c: meta.get(c, ("", "Unknown"))[1])

    probs = predictor.predict(kf) if predictor is not None else None
    if probs is not None:
        logger.info(f"챔피언 모델 예측: {len(probs)} 종목")

    filtered = filter_candidates(
        kf, min_trading_value=min_trading_value, min_price=min_price)
    logger.info(f"하드 필터 통과: {len(filtered)} 종목")

    if filtered.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    scored = score_candidates(filtered, probs=probs)
    ranked = rank_candidates(scored, top_n=top_n)
    # Reorder to the output contract (missing columns get '' / NaN).
    for col in OUTPUT_COLUMNS:
        if col not in ranked.columns:
            ranked[col] = ""
    return ranked[OUTPUT_COLUMNS + [c for c in ranked.columns if c not in OUTPUT_COLUMNS]].copy()
