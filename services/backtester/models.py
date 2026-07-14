"""Pydantic models for backtesting results."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SimulationParams(BaseModel):
    stock_code: str
    lookback_days: int = 252
    n_simulations: int = 10000
    confidence_level: float = 0.95
    risk_free_rate: float = 0.02


class SimulationMetrics(BaseModel):
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


class BacktestResult(BaseModel):
    id: Optional[int] = None
    stock_code: str
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
    created_at: Optional[datetime] = None


class BatchBacktestResult(BaseModel):
    results: Dict[str, BacktestResult]
    total_stocks: int
    failed_stocks: List[str] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=datetime.now)
