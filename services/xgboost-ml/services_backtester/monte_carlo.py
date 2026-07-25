"""Monte Carlo simulation engine for portfolio risk analysis."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

try:
    from .models import BacktestResult, SimulationParams
except ImportError:
    from models import BacktestResult, SimulationParams

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    n_simulations: int
    confidence_level: float
    var_95: float
    var_99: float
    cvar_95: float
    expected_return: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    win_rate: float
    volatility: float
    total_return: float


class MonteCarloEngine:
    """
    Monte Carlo simulation using Geometric Brownian Motion.
    """

    def __init__(self, db_connection: Optional[object] = None):
        self.db = db_connection

    def run_simulation(
        self,
        stock_code: str,
        lookback_days: int = 252,
        n_simulations: int = 10000,
        confidence_level: float = 0.95,
        risk_free_rate: float = 0.02,
    ) -> SimulationResult:
        """
        Run Monte Carlo simulation for a stock.

        Steps:
        1. Fetch historical returns from DB or generate sample data
        2. Calculate drift (mu) and volatility (sigma)
        3. Generate n_simulations GBM paths for lookback_days
        4. Calculate metrics: VaR, CVaR, Sharpe, Sortino, max drawdown

        Returns SimulationResult with all metrics.
        """
        try:
            daily_returns = self._fetch_historical_returns(stock_code, lookback_days)
            mu = float(np.mean(daily_returns))
            sigma = float(np.std(daily_returns))

            final_prices, all_paths = self._run_gbm(
                mu, sigma, lookback_days, n_simulations
            )

            return self._compute_metrics(
                final_prices, all_paths, confidence_level, risk_free_rate,
                n_simulations, 100.0,
            )
        except Exception as e:
            logger.error(f"Simulation failed for {stock_code}: {e}")
            return SimulationResult(
                n_simulations=0, confidence_level=0.0,
                var_95=0.0, var_99=0.0, cvar_95=0.0,
                expected_return=0.0, max_drawdown=0.0,
                sharpe_ratio=0.0, sortino_ratio=0.0,
                win_rate=0.0, volatility=0.0, total_return=0.0,
            )

    def _fetch_historical_returns(
        self, stock_code: str, lookback_days: int
    ) -> np.ndarray:
        if self.db is not None:
            try:
                import pandas as pd
                query = """
                    SELECT close_price FROM market_data
                    WHERE stock_code = %s
                    ORDER BY trade_date DESC
                    LIMIT %s
                """
                with self.db.cursor() as cur:
                    cur.execute(query, (stock_code, lookback_days + 1))
                    rows = cur.fetchall()
                if len(rows) > 1:
                    prices = pd.Series([float(r[0]) for r in rows][::-1])
                    returns = prices.pct_change().dropna().values
                    return np.array(returns, dtype=np.float64)
            except Exception as e:
                logger.warning(f"DB fetch failed for {stock_code}, using synthetic data: {e}")

        np.random.seed(42)
        return np.random.normal(0.0005, 0.02, lookback_days)

    def _run_gbm(
        self, mu: float, sigma: float,
        lookback_days: int, n_simulations: int,
    ):
        np.random.seed(42)
        dt = 1.0
        drift = (mu - 0.5 * sigma ** 2) * dt
        vol = sigma * np.sqrt(dt)

        random_shocks = np.random.normal(
            0, 1, (n_simulations, lookback_days)
        )
        log_returns = drift + vol * random_shocks
        price_paths = 100.0 * np.exp(np.cumsum(log_returns, axis=1))
        final_prices = price_paths[:, -1]
        return final_prices, price_paths

    def _compute_metrics(
        self, final_prices: np.ndarray, all_paths: np.ndarray,
        confidence_level: float, risk_free_rate: float,
        n_simulations: int, initial_price: float,
    ) -> SimulationResult:
        returns_array = (final_prices - initial_price) / initial_price

        var_95 = float(np.percentile(returns_array, (1 - confidence_level) * 100))
        var_99 = float(np.percentile(returns_array, 1))

        cvar_95 = float(np.mean(returns_array[returns_array <= var_95]))

        expected_return = float(np.median(returns_array))

        peaks = np.maximum.accumulate(all_paths, axis=1)
        drawdowns = (all_paths - peaks) / peaks
        max_drawdown = float(np.min(drawdowns))

        daily_rf = risk_free_rate / 252
        excess_returns = returns_array - daily_rf
        excess_std = float(np.std(excess_returns))
        if excess_std > 0:
            sharpe_ratio = float(np.mean(excess_returns) / excess_std * np.sqrt(252))
        else:
            sharpe_ratio = 0.0

        downside = excess_returns[excess_returns < 0]
        downside_std = float(np.std(downside)) if len(downside) > 0 else 0.001
        sortino_ratio = float(np.mean(excess_returns) / downside_std * np.sqrt(252))

        win_rate = float(np.mean(returns_array > 0))

        simulated_returns = np.diff(np.log(all_paths), axis=1)
        volatility = float(np.std(simulated_returns) * np.sqrt(252))
        total_return = float(np.median(returns_array))

        return SimulationResult(
            n_simulations=n_simulations,
            confidence_level=confidence_level,
            var_95=var_95,
            var_99=var_99,
            cvar_95=cvar_95,
            expected_return=expected_return,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            win_rate=win_rate,
            volatility=volatility,
            total_return=total_return,
        )

    def run_batch(
        self,
        stock_codes: List[str],
        lookback_days: int = 252,
        n_simulations: int = 1000,
    ) -> Dict[str, SimulationResult]:
        results = {}
        for code in stock_codes:
            try:
                results[code] = self.run_simulation(
                    code, lookback_days, n_simulations
                )
            except Exception as e:
                logger.error(f"Simulation failed for {code}: {e}")
        return results

    def save_result(self, result: SimulationResult, stock_code: str) -> Optional[int]:
        try:
            if self.db is None:
                logger.info(f"Simulation result for {stock_code} (no DB configured)")
                return None

            params = SimulationParams(stock_code=stock_code)
            model = BacktestResult(
                stock_code=stock_code,
                n_simulations=result.n_simulations,
                confidence_level=result.confidence_level,
                var_95=result.var_95,
                var_99=result.var_99,
                cvar_95=result.cvar_95,
                expected_return=result.expected_return,
                max_drawdown=result.max_drawdown,
                sharpe_ratio=result.sharpe_ratio,
                sortino_ratio=result.sortino_ratio,
                win_rate=result.win_rate,
                volatility=result.volatility,
                total_return=result.total_return,
            )

            with self.db.cursor() as cur:
                cur.execute("""
                    INSERT INTO backtest_results (
                        stock_code, n_simulations, confidence_level,
                        var_95, var_99, cvar_95, expected_return,
                        max_drawdown, sharpe_ratio, sortino_ratio,
                        win_rate, volatility, total_return
                    ) VALUES (
                        %(stock_code)s, %(n_simulations)s, %(confidence_level)s,
                        %(var_95)s, %(var_99)s, %(cvar_95)s, %(expected_return)s,
                        %(max_drawdown)s, %(sharpe_ratio)s, %(sortino_ratio)s,
                        %(win_rate)s, %(volatility)s, %(total_return)s
                    )
                    ON CONFLICT (stock_code, created_at) DO NOTHING
                    RETURNING id
                """, model.model_dump())
                self.db.commit()
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"Failed to save result for {stock_code}: {e}")
            return None
