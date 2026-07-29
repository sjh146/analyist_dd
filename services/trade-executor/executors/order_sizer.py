from __future__ import annotations

from abc import ABC, abstractmethod
from math import isclose


class OrderSizer(ABC):
    @abstractmethod
    def calculate(
        self, balance: float, price: float, confidence: float = 0.5, **kwargs
    ) -> int:
        ...


class FixedSizer(OrderSizer):
    def __init__(self, fixed_qty: int = 10):
        self.fixed_qty = fixed_qty

    def calculate(
        self, balance: float, price: float, confidence: float = 0.5, **kwargs
    ) -> int:
        return max(1, self.fixed_qty)


class PercentSizer(OrderSizer):
    def __init__(self, percent: float = 0.1):
        self.percent = percent

    def calculate(
        self, balance: float, price: float, confidence: float = 0.5, **kwargs
    ) -> int:
        if price <= 0 or balance <= 0:
            return 1
        qty = int(balance * self.percent / price)
        return max(1, qty)


class KellySizer(OrderSizer):
    def __init__(self, win_rate: float, avg_win: float, avg_loss: float):
        self.win_rate = win_rate
        self.avg_win = avg_win
        self.avg_loss = avg_loss

    def calculate(
        self, balance: float, price: float, confidence: float = 0.5, **kwargs
    ) -> int:
        p = self.win_rate
        q = 1.0 - p
        b = self.avg_win / self.avg_loss if self.avg_loss > 0 else 0.0

        if b <= 0 or isclose(p, 0.0) or isclose(p, 1.0):
            fraction = 0.0
        else:
            fraction = p - q / b

        fraction = max(0.0, min(0.25, fraction))

        if price <= 0 or balance <= 0:
            return 1
        qty = int(balance * fraction / price)
        return max(1, qty)


class VolatilitySizer(OrderSizer):
    def __init__(self, atr_period: int = 20, risk_per_trade: float = 0.02):
        self.atr_period = atr_period
        self.risk_per_trade = risk_per_trade

    def calculate(
        self, balance: float, price: float, confidence: float = 0.5, **kwargs
    ) -> int:
        atr = kwargs.get('atr', 0.0)
        if atr <= 0 or price <= 0 or balance <= 0:
            return 1
        risk_amount = balance * self.risk_per_trade
        qty = int(risk_amount / atr)
        return max(1, qty)


class RiskParitySizer(OrderSizer):
    def __init__(self, n_positions: int = 10):
        self.n_positions = n_positions

    def calculate(
        self, balance: float, price: float, confidence: float = 0.5, **kwargs
    ) -> int:
        if price <= 0 or balance <= 0 or self.n_positions <= 0:
            return 1
        qty = int(balance / self.n_positions / price)
        return max(1, qty)
