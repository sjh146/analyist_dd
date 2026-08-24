"""Day-trading screener core: kalman features, scoring and ranking.

Scoring engine (0–100, per docs/단타스크리너_PLAN.md §4):

    kalman_trend  30  : noise-free slope normalised cross-section + low-noise bonus
    model         30  : (champion_prob − 0.5) × 2 × 30, 0 when unavailable
    volume_surge  20  : min(volume_surge / 3.0, 1.0) × 20
    vol_fit       20  : low residual noise & not-too-chaotic annualised vol

All pure functions — no DB access — so they can be unit-tested with fixtures.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("day_trading_engine.scoring")

ChampionPredictor = Callable[[pd.DataFrame], Optional[pd.Series]]
"""Signature: rows (per signal-date stock) → per-stock up-probability Series
indexed by stock_code, or None if the model is unavailable."""

# Default scoring handles (kept in one place for tests/CLI parity).
WEIGHTS = {
    "kalman_trend": 30.0,
    "model": 30.0,
    "volume_surge": 20.0,
    "vol_fit": 20.0,
}


def _clamp(x: float, lo: float, hi: float) -> float:
    if isinstance(x, float) and np.isnan(x):
        return lo
    return max(lo, min(hi, float(x)))


def compute_kalman_features(df: pd.DataFrame,
                            smoother_factory: Optional[Callable] = None,
                            slope_window: int = 5) -> pd.DataFrame:
    """Add denoised kalman trend columns per `stock_code`.

    Input ``df`` must have OHLCV layout (providers.OHLCV_COLUMNS) and be sorted
    by ``(stock_code, trade_date)`` ascending.  Adds one row per stock with:

        kalman_slope, kalman_trend, noise_resid_std, volatility_ann,
        volume_surge, volume_ratio_5, day_change
    """
    if "volume_surge" not in df.columns:
        df["volume_surge"] = np.nan
    if "volatility_ann" not in df.columns:
        df["volatility_ann"] = np.nan
    if "noise_resid_std" not in df.columns:
        df["noise_resid_std"] = np.nan
    if "kalman_trend" not in df.columns:
        df["kalman_trend"] = np.nan
    if "kalman_slope" not in df.columns:
        df["kalman_slope"] = np.nan

    if smoother_factory is None:
        try:
            from app.feature_engine.kalman_smoother import KalmanSmoother
            smoother_factory = KalmanSmoother
        except Exception:  # pragma: no cover - import fallback only
            # Locate services/xgboost-ml relative to the engine package so the
            # screener runs both from scripts/ and from tests/.
            engine_dir = os.path.dirname(os.path.abspath(__file__))  # scripts/day_trading_engine
            scripts_dir = os.path.dirname(engine_dir)
            repo_root = os.path.dirname(scripts_dir)
            xgb_root = os.path.join(repo_root, "services", "xgboost-ml")
            if xgb_root not in sys.path:
                sys.path.insert(0, xgb_root)
            try:
                from app.feature_engine.kalman_smoother import KalmanSmoother
                smoother_factory = KalmanSmoother
            except Exception as exc:  # pragma: no cover - import fallback only
                raise RuntimeError(f"KalmanSmoother cannot be imported: {exc}")

    rows = []
    for code, grp in df.groupby("stock_code"):
        grp = grp.sort_values("trade_date")
        closes = grp["close_price"].to_numpy(dtype=float)
        closes = closes[np.isfinite(closes)]

        smoother = smoother_factory()
        sm = smoother.smooth(closes)

        last = grp.iloc[-1]
        prev5 = grp.iloc[-6:-1] if len(grp) >= 6 else grp.iloc[:-1]
        mean_prev_vol = float(prev5["volume"].mean()) if len(prev5) else 0.0
        vol_now = float(last["volume"]) if np.isfinite(last["volume"]) else 0.0
        volume_surge = vol_now / mean_prev_vol if mean_prev_vol > 0 else np.nan

        day_change = np.nan
        if len(grp) >= 2:
            prev_close = float(grp.iloc[-2]["close_price"])
            if prev_close and np.isfinite(prev_close):
                day_change = float(last["close_price"]) / prev_close - 1.0

        rows.append({
            "stock_code": code,
            "signal_date": str(last["trade_date"]),
            "close_price": float(last["close_price"]) if np.isfinite(last["close_price"]) else np.nan,
            "volume": vol_now,
            "trading_value": float(last["trading_value"]) if "trading_value" in grp and np.isfinite(last["trading_value"]) else np.nan,
            "kalman_trend": float(sm["trend"]),
            "kalman_slope": float(sm["slope"]),
            "noise_resid_std": float(sm["noise_resid_std"]),
            "volatility_ann": float(sm["volatility_ann"]),
            "volume_surge": volume_surge,
            "day_change": day_change,
        })
    return pd.DataFrame(rows)


def score_candidates(kf: pd.DataFrame,
                     probs: Optional[pd.Series] = None) -> pd.DataFrame:
    """Compute the 0–100 score for each denoised feature row.

    Parameters
    ----------
    kf : frame from :func:`compute_kalman_features`.
    probs : optional Series indexed by stock_code of up-probabilities.
    """
    if kf.empty:
        cols = list(kf.columns) + ["model_prob", "score", "reason"]
        return pd.DataFrame(columns=cols)

    # Cross-sectional normalisation of kalman trend (smoothed current momentum).
    # This is the denoised drift — the primary trend-strength signal.
    trends = kf["kalman_trend"].to_numpy(dtype=float)
    f_t = trends[np.isfinite(trends)]
    if f_t.size > 1:
        mu_t, sd_t = float(f_t.mean()), float(f_t.std())
        if sd_t > 0:
            z_trend = (trends - mu_t) / sd_t
        else:
            z_trend = np.zeros_like(trends)
    else:
        z_trend = np.zeros_like(trends)

    # Cross-sectional normalisation of kalman slope (acceleration; tiebreak).
    slopes = kf["kalman_slope"].to_numpy(dtype=float)
    f_s = slopes[np.isfinite(slopes)]
    if f_s.size > 1:
        mu_s, sd_s = float(f_s.mean()), float(f_s.std())
        if sd_s > 0:
            z_slope = (slopes - mu_s) / sd_s
        else:
            z_slope = np.zeros_like(slopes)
    else:
        z_slope = np.zeros_like(slopes)

    # Noise residual std cross-section (lower = cleaner trend → bonus).
    resid = kf["noise_resid_std"].to_numpy(dtype=float)
    f2 = resid[np.isfinite(resid)]
    resid_ref = float(f2.mean()) + float(f2.std()) if f2.size > 1 else 0.01

    rows = []
    for idx, (_, r) in enumerate(kf.iterrows()):
        # 1) kalman trend pts — momentum(z_trend) 10 + slope tiebreak 5 + noise bonus 15.
        momentum_pts = (_clamp(z_trend[idx], -1.0, 1.0) * 0.5 + 0.5) * 10.0
        accel_pts = (_clamp(z_slope[idx], -1.0, 1.0) * 0.5 + 0.5) * 5.0
        noise = r["noise_resid_std"]
        if noise is None or (isinstance(noise, float) and np.isnan(noise)):
            noise_bonus = 0.0
        else:
            noise_bonus = (1.0 - _clamp(float(noise) / resid_ref, 0.0, 1.0)) * 15.0
        kalman_pts = _clamp(momentum_pts + accel_pts + noise_bonus, 0.0, 30.0)

        # 2) model pts
        model_pts = 0.0
        prob = None
        if probs is not None and r["stock_code"] in probs.index:
            prob = float(probs.loc[r["stock_code"]])
            model_pts = _clamp((prob - 0.5) * 2.0, 0.0, 1.0) * WEIGHTS["model"]

        # 3) volume surge
        surge = r["volume_surge"]
        if surge is None or (isinstance(surge, float) and np.isnan(surge)):
            vol_pts = 0.0
        else:
            vol_pts = min(float(surge) / 3.0, 1.0) * WEIGHTS["volume_surge"]

        # 4) volatility fit — reward cleaner, directionally meaningful series.
        v_ann = r["volatility_ann"]
        if v_ann is None or (isinstance(v_ann, float) and np.isnan(v_ann)):
            vol_fit_pts = 0.0
        else:
            v = float(v_ann)
            # target-band peak around 40–80% ann.; penalise both extremes.
            fit = np.exp(-0.5 * ((v - 0.6) / 0.35) ** 2) if v > 0 else 0.0
            vol_fit_pts = fit * WEIGHTS["vol_fit"]

        score = _clamp(kalman_pts + model_pts + vol_pts + vol_fit_pts, 0.0, 100.0)

        # reason string (deterministic)
        parts = []
        if prob is None:
            parts.append("모델미가용")
        else:
            parts.append(f"모델 {prob:.2f}")
        parts.append(f"칼만추세 {r['kalman_trend'] * 1000:.1f}‰")
        parts.append(f"기울기 {r['kalman_slope'] * 1000:.1f}‰")
        if not (surge is None or (isinstance(surge, float) and np.isnan(surge))):
            parts.append(f"거래량 {surge:.1f}배")
        parts.append(f"잔차노이즈 {noise:.4f}")
        reason = ", ".join(parts)

        out_row = {
            "stock_code": r["stock_code"],
            "signal_date": r["signal_date"],
            "close_price": r["close_price"],
            "volume": r["volume"],
            "trading_value": r["trading_value"],
            "kalman_trend": r["kalman_trend"],
            "kalman_slope": r["kalman_slope"],
            "noise_resid_std": r["noise_resid_std"],
            "volatility_ann": r["volatility_ann"],
            "volume_surge": r["volume_surge"],
            "day_change": r["day_change"],
            "model_prob": prob,
            "score": score,
            "reason": reason,
        }
        for extra in ("stock_name", "sector"):
            if extra in kf.columns:
                out_row[extra] = r[extra]
        rows.append(out_row)
    return pd.DataFrame(rows)


def filter_candidates(candidates: pd.DataFrame,
                      min_trading_value: float = 300_000_000,
                      min_price: float = 1000) -> pd.DataFrame:
    """Hard filters: liquidity, min price and valid indicators."""
    required = ["kalman_slope", "kalman_trend", "volume_surge", "volatility_ann",
                "noise_resid_std"]
    mask = pd.Series(True, index=candidates.index)
    for col in required:
        mask &= candidates[col].notna()
    tv = candidates["trading_value"]
    effective_tv = tv.where(tv.notna(), candidates["close_price"] * candidates["volume"])
    mask &= effective_tv >= min_trading_value
    mask &= candidates["close_price"] >= min_price
    mask &= candidates["volume"] > 0
    return candidates[mask].copy()


def rank_candidates(candidates: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """Sort by score desc, assign rank 1..n, truncate to top_n."""
    if candidates.empty:
        out = candidates.copy()
        out["rank"] = []
        return out
    out = candidates.sort_values("score", ascending=False).reset_index(drop=True)
    out = out.head(top_n).copy()
    out["rank"] = range(1, len(out) + 1)
    return out
