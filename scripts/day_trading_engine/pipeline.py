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
from typing import Any, Optional

import numpy as np
import pandas as pd

from .providers import MarketDataProvider, StockInfo
from .scoring import (compute_kalman_features, filter_candidates,
                      prefilter_liquid, rank_candidates, score_candidates)

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
        return os.environ.get("MODEL_DIR") or os.path.join(
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
        codes = [str(c) for c in df["stock_code"].tolist()]
        # 지연 최적화(2026-11-18): 예전에는 종목마다 ``predict(1×F)`` 를 호출해
        # 1,858회의 모델 호출이 발생했다. 특징행렬을 한 번에 만들어 **1회 배치 호출**한다.
        # 실패하면 기존 행별 경로로 폴백(동작 보존).
        try:
            X = np.zeros((len(df), len(fnames)), dtype=np.float32)
            for i, (_, row) in enumerate(df.iterrows()):
                code = str(row["stock_code"])
                for j, f in enumerate(fnames):
                    X[i, j] = self._feature_value(row, f, code)
            X = np.nan_to_num(X, nan=0.0)
            batch = np.asarray(self._ensemble.predict(X), dtype=float).reshape(-1)
            if batch.shape[0] == len(codes):
                return pd.Series(dict(zip(codes, batch)), dtype=float)
            logger.debug("batch predict shape mismatch (%s vs %s) — 행별 폴백",
                         batch.shape[0], len(codes))
        except Exception as e:  # pragma: no cover
            logger.debug("batch predict failed (%s) — 행별 폴백", e)

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


def normalize_code_value(value: Any) -> str:
    """'A003780' / 3780 / ' 003780 ' → '003780' (6자리 숫자 문자열, 불가하면 '')."""
    import re as _re

    text = str(value or "").strip().lstrip("Aa").strip()
    if text.isdigit() and len(text) < 6:
        text = text.zfill(6)
    return text if _re.match(r"^\d{6}$", text) else ""


def run_screener(provider: MarketDataProvider,
                 top_n: int = 20,
                 lookback: int = 20,
                 min_history: int = 20,
                 min_trading_value: float = 5_000_000_000,
                 min_price: float = 1000,
                 date_str: str | None = None,
                 predictor: Optional[ChampionPredictor] = None,
                 allow_codes: Optional[set] = None,
                 exclude_codes: Optional[set] = None,
                 bonus_codes: Optional[set] = None,
                 bonus_points: float = 5.0,
                 theme_points: Optional[dict] = None) -> pd.DataFrame:
    """Full pipeline: universe → lookback → kalman → (model) → score → rank.

    크레온 상승률 랭킹 사용 방식(2026-11-18 실측 후 확정 — 사용자 선택 2번):

    * ``exclude_codes``: **추격 배제**. 이미 +12% 이상 오른 종목을 유니버스에서 제거한다.
    * ``bonus_codes``: 상승률 2~12% 대역 종목에 ``bonus_points`` 를 **정렬 가점**으로 준다.
      공개 ``score`` 값은 **바꾸지 않는다**(트레이더 R1 문턱의 의미 보존) — 순위만 바뀐다.
    * ``allow_codes``: (구) 유니버스 압축. 교집합이 0건이 되어 '압축'이 아니라 '대체'가
      되므로 기본은 쓰지 않는다(실측 data/reports/rank_universe_overlap.json).

    Returns a ranked DataFrame in :data:`OUTPUT_COLUMNS` order (may be empty).
    """
    signal_date = provider.resolve_signal_date(date_str)
    logger.info(f"시그널 기준일: {signal_date}")

    universe = provider.get_universe(min_history=min_history)
    if allow_codes:
        before = len(universe)
        wanted = {normalize_code_value(c) for c in allow_codes}
        wanted.discard("")
        universe = [s for s in universe if s.stock_code in wanted]
        logger.info(
            "유니버스 압축(rank_universe): %d → %d 종목 (요청 %d 코드)",
            before, len(universe), len(wanted))
    if exclude_codes:
        def _norm(v: Any) -> str:
            return normalize_code_value(v)

        drop = {_norm(c) for c in exclude_codes}
        drop.discard("")
        before = len(universe)
        universe = [s for s in universe if s.stock_code not in drop]
        logger.info("추격 배제(상승률 상단): %d → %d 종목 (%d 코드 제외)",
                    before, len(universe), len(drop))

    universe_codes = {s.stock_code for s in universe}
    logger.info(f"유니버스: {len(universe)} 종목")

    meta = {s.stock_code: (s.stock_name, s.sector) for s in universe}

    # 유니버스 코드만 조회(lateral + ANY) — 전체 시장(3,974 코드) 대신 1,858 코드.
    lookback_df = provider.load_lookback(signal_date, lookback=lookback,
                                        codes=sorted(universe_codes))
    lookback_df["stock_code"] = lookback_df["stock_code"].astype(str)
    lookback_df = lookback_df[lookback_df["stock_code"].isin(universe_codes)]
    logger.info(f"룩백 로드: {len(lookback_df)} 행 / {lookback_df['stock_code'].nunique()} 종목")

    # 사전 유동성 필터로 칼만 대상 축소(2026-11-18 지연 최적화: 1,858 → 669 종목).
    last_rows = (lookback_df.sort_values(["stock_code", "trade_date"])
                 .groupby("stock_code", as_index=False).tail(1))
    pre = prefilter_liquid(last_rows, min_trading_value=min_trading_value,
                           min_price=min_price)
    keep_codes = set(pre["stock_code"].astype(str))
    if keep_codes and len(keep_codes) < lookback_df["stock_code"].nunique():
        logger.info("사전 유동성 필터: %d → %d 종목 (칼만 계산 축소)",
                    lookback_df["stock_code"].nunique(), len(keep_codes))
        lookback_df = lookback_df[lookback_df["stock_code"].astype(str).isin(keep_codes)]

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
    if bonus_codes:
        boost = {normalize_code_value(c) for c in bonus_codes}
        boost.discard("")
        if boost:
            scored = scored.copy()
            hit = scored["stock_code"].astype(str).isin(boost)
            # 정렬 전용 가점: 공개 score 는 그대로 두고 순위만 올린다.
            scored["_rank_score"] = scored["score"] + np.where(hit, float(bonus_points), 0.0)
            logger.info("상승률 대역 가점: %d/%d 종목에 +%.1f점(정렬 전용)",
                        int(hit.sum()), len(scored), float(bonus_points))
    if theme_points:
        scored = scored.copy()
        extra = scored["stock_code"].astype(str).map(
            {str(k): float(v) for k, v in theme_points.items()}).fillna(0.0)
        base = scored["_rank_score"] if "_rank_score" in scored.columns else scored["score"]
        scored["_rank_score"] = base + extra
        logger.info("테마 가점: %d/%d 종목(정렬 전용)", int((extra > 0).sum()), len(scored))
    ranked = rank_candidates(scored, top_n=top_n)
    if "_rank_score" in ranked.columns:
        ranked = ranked.drop(columns=["_rank_score"])
    # Reorder to the output contract (missing columns get '' / NaN).
    for col in OUTPUT_COLUMNS:
        if col not in ranked.columns:
            ranked[col] = ""
    return ranked[OUTPUT_COLUMNS + [c for c in ranked.columns if c not in OUTPUT_COLUMNS]].copy()
