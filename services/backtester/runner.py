"""Backtesting pipeline using real Feature Pipeline + Ensemble Model predictions."""

import sys
import os
import logging
from datetime import date, datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Ensure xgboost-ml is importable
_xgboost_ml_path = os.path.join(os.path.dirname(__file__), '..', 'xgboost-ml')
if _xgboost_ml_path not in sys.path:
    sys.path.insert(0, os.path.abspath(_xgboost_ml_path))

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel

logger = logging.getLogger(__name__)

PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "stock_secure_password_2026")


@dataclass
class BacktestTrade:
    date: date
    stock_code: str
    signal: str
    confidence: float
    predicted_return: float
    actual_return: float
    pnl: float


@dataclass
class BacktestResult:
    strategy: str
    start_date: date
    end_date: date
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    num_trades: int
    trades: List[BacktestTrade] = field(default_factory=list)


class BacktestRunner:
    """
    Run backtests using actual Feature Pipeline + Ensemble Model predictions.
    Paper trading mode: generates signals without executing real trades.
    """

    def __init__(self, pg_conn=None, model_dir: str = None):
        self.paper_mode = True
        if model_dir is None:
            model_dir = os.path.join(
                os.path.dirname(__file__), '..', 'xgboost-ml', 'app', 'models', 'saved_models'
            )
        self.model_dir = os.path.abspath(model_dir)

        if pg_conn is not None:
            self.pg_conn = pg_conn
            self._owns_conn = False
        else:
            import psycopg2
            self.pg_conn = psycopg2.connect(
                host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                user=PG_USER, password=PG_PASS,
            )
            self._owns_conn = True

        self.pipeline = FeaturePipeline(pg_conn=self.pg_conn)
        self.ensemble = EnsembleModel(model_dir=self.model_dir)
        self.ensemble.load(self.model_dir)

    def run_backtest(
        self,
        strategy: str,
        stock_codes: List[str],
        start_date: str,
        end_date: str,
    ) -> BacktestResult:
        """
        Run backtest for a strategy using real features + ML predictions.

        1. Load trained EnsembleModel
        2. Build features for each stock/date via FeaturePipeline
        3. Predict up-probability for each row
        4. Generate buy signals when confidence >= 0.65
        5. Calculate actual returns from real price data
        6. Return BacktestResult with trades and metrics
        """
        try:
            # 1. Build feature matrix for backtest period
            logger.info(f"Building features for {len(stock_codes)} stocks: {start_date} to {end_date}")
            df = self.pipeline.build_training_features(stock_codes, start_date, end_date)

            if df is None or df.empty:
                logger.warning("No feature data returned for backtest period")
                return self._empty_result(strategy, start_date, end_date)

            # 2. Extract feature names from saved model features
            saved_features = self.ensemble.load_feature_names(self.model_dir)
            available_features = [f for f in saved_features if f in df.columns]

            if len(available_features) < 5:
                logger.warning(f"Too few features available: {len(available_features)}")
                return self._empty_result(strategy, start_date, end_date)

            X = df[available_features].values.astype(np.float32)
            X = np.nan_to_num(X, nan=0.0)

            # 3. Predict up-probability for all rows
            probs = self.ensemble.predict(X)

            # 4. Generate trades from high-confidence buy signals
            trades = []
            daily_returns = []

            # Group by stock_code for actual return calculation
            if 'stock_code' in df.columns and 'price' in df.columns:
                for stock_code in stock_codes:
                    mask = df['stock_code'] == stock_code
                    stock_df = df[mask].copy()
                    stock_probs = probs[mask.values]
                    stock_indices = df.index[mask].tolist()

                    if stock_df.empty:
                        continue

                    prices = stock_df['price'].values
                    stock_dates = stock_df['date'].values if 'date' in stock_df.columns else [None] * len(stock_df)

                    for i in range(len(stock_df)):
                        prob = float(stock_probs[i])
                        if prob >= 0.65:
                            # Buy signal — actual return over next 5 days
                            if i + 5 < len(prices) and prices[i] > 0:
                                actual_ret = float((prices[min(i + 5, len(prices) - 1)] - prices[i]) / prices[i])
                            else:
                                actual_ret = 0.0

                            trade_date_val = stock_dates[i]
                            if isinstance(trade_date_val, str):
                                trade_date = date.fromisoformat(trade_date_val)
                            elif hasattr(trade_date_val, 'date'):
                                trade_date = trade_date_val.date()
                            else:
                                trade_date = date.today()

                            predicted_ret = float(prob - 0.5)
                            pnl = actual_ret  # Long-only: PnL = actual return

                            trade = BacktestTrade(
                                date=trade_date,
                                stock_code=stock_code,
                                signal='buy',
                                confidence=prob,
                                predicted_return=predicted_ret,
                                actual_return=actual_ret,
                                pnl=pnl,
                            )
                            trades.append(trade)
                            daily_returns.append(actual_ret)

            if not daily_returns:
                logger.warning("No trades generated — no signals met confidence threshold")
                return self._empty_result(strategy, start_date, end_date)

            returns_array = np.array(daily_returns)

            # 5. Calculate portfolio metrics
            total_return = float(np.sum(returns_array))
            mean_ret = float(np.mean(returns_array))
            std_ret = float(np.std(returns_array))
            sharpe = (mean_ret / (std_ret + 1e-8)) * np.sqrt(252)

            cumulative = np.cumprod(1 + returns_array)
            peak = np.maximum.accumulate(cumulative)
            drawdown = (cumulative - peak) / peak
            max_dd = float(np.min(drawdown))

            win_rate = float(np.mean(returns_array > 0))

            logger.info(
                f"Backtest {strategy} completed: {len(trades)} trades, "
                f"return={total_return:.4f}, sharpe={sharpe:.4f}, "
                f"win_rate={win_rate:.4f}, max_dd={max_dd:.4f}"
            )

            return BacktestResult(
                strategy=strategy,
                start_date=date.fromisoformat(start_date),
                end_date=date.fromisoformat(end_date),
                total_return=total_return,
                sharpe_ratio=sharpe,
                max_drawdown=max_dd,
                win_rate=win_rate,
                num_trades=len(trades),
                trades=trades,
            )

        except Exception as e:
            logger.exception(f"Backtest {strategy} failed: {e}")
            return self._empty_result(strategy, start_date, end_date)

    def _empty_result(self, strategy: str, start_date: str, end_date: str) -> BacktestResult:
        """Return an empty BacktestResult on failure."""
        return BacktestResult(
            strategy=strategy,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            total_return=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            num_trades=0,
        )
