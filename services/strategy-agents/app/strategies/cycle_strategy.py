"""
Cycle Rotation Strategy
Detects market phases and rotates into early-cycle sectors.
"""

import numpy as np
import logging
from typing import List, Dict
from app.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class CycleStrategy(BaseStrategy):
    def __init__(self, storage):
        super().__init__("cycle_rotation", storage)
        self.lookback_days = self.config.get("lookback_days", 60)
        self.rotation_threshold = self.config.get("rotation_threshold", 0.1)

    def analyze(self) -> List[Dict]:
        """
        Analyze sector rotation and generate rotation signals.
        
        Returns:
            List of trade signals
        """
        signals = []

        # Detect market phase
        market_phase = self._detect_market_phase()
        logger.info(f"Current market phase: {market_phase}")

        # Get sector momentum rankings
        sectors = self._get_sector_momentum()
        if not sectors:
            return signals

        # Find rotating sectors
        early_cycle_sectors = [
            s for s in sectors
            if s["phase"] == "early" and s["momentum"] > self.rotation_threshold
        ]

        late_cycle_sectors = [
            s for s in sectors
            if s["phase"] == "late"
        ]

        # Generate rotation signals
        for sector in early_cycle_sectors[:2]:  # Top 2 early-cycle sectors
            top_stocks = self.storage.get_top_stocks_in_sector(
                sector["name"], top_n=3
            )
            for stock in top_stocks:
                signals.append({
                    "action": "buy",
                    "stock_code": stock["stock_code"],
                    "price": 0,
                    "reason": f"Sector rotation: {sector['name']} in early cycle (momentum={sector['momentum']:.2f})",
                    "strategy_name": "cycle_rotation",
                    "confidence": min(0.9, abs(sector["momentum"])),
                })

        # Generate sell signals for late-cycle sectors
        for sector in late_cycle_sectors[:2]:
            top_stocks = self.storage.get_top_stocks_in_sector(
                sector["name"], top_n=2
            )
            for stock in top_stocks:
                signals.append({
                    "action": "sell",
                    "stock_code": stock["stock_code"],
                    "price": 0,
                    "reason": f"Sector rotation: {sector['name']} in late cycle",
                    "strategy_name": "cycle_rotation",
                    "confidence": 0.7,
                })

        return signals

    def _detect_market_phase(self) -> str:
        """Detect current market phase."""
        try:
            index_returns = self.storage.get_index_return(self.lookback_days)
            index_vol = self.storage.get_index_volatility(self.lookback_days)

            if index_returns > 0.1 and index_vol < 0.2:
                return "bull"
            elif index_returns < -0.1:
                return "bear"
            else:
                return "sideways"
        except Exception:
            return "unknown"

    def _get_sector_momentum(self) -> List[Dict]:
        """Get momentum for each sector."""
        sectors = self.storage.get_sectors()
        result = []
        for sector in sectors:
            momentum = self.storage.get_sector_momentum(
                sector["name"], self.lookback_days
            )
            if momentum is not None:
                phase = "early" if momentum > 0.05 else "late" if momentum < -0.05 else "mid"
                result.append({
                    "name": sector["name"],
                    "momentum": momentum,
                    "phase": phase,
                })
        result.sort(key=lambda x: abs(x["momentum"]), reverse=True)
        return result
