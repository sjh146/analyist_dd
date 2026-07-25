"""Paper Trading Gate — paper mode by default, auto-switch evaluation."""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GateStatus:
    mode: str
    sharpe_ratio: float
    consecutive_profitable_days: int
    ready_for_real: bool
    reason: str


class PaperTradingGate:
    """
    Paper Trading Gate.
    
    - Always starts in paper mode.
    - Evaluates performance: if Sharpe > 1.0 for 30+ consecutive days, 
      suggests switching to real mode.
    - Switching to real requires human approval (never automatic).
    """
    
    def __init__(self):
        self.mode = 'paper'
        self.performance_history: list = []
        self.daily_pnl: list = []
        self.consecutive_profitable_days = 0
    
    def evaluate(self) -> GateStatus:
        """
        Evaluate current performance and determine if ready for real trading.
        
        Returns GateStatus with mode, metrics, and readiness.
        """
        try:
            import numpy as np
            
            daily_returns = np.array(self.daily_pnl) if self.daily_pnl else np.array([])
            
            if len(daily_returns) > 1:
                sharpe = np.mean(daily_returns) / (np.std(daily_returns) + 1e-8) * np.sqrt(252)
            else:
                sharpe = 0.0
            
            profitable_count = 0
            for pnl in reversed(self.daily_pnl):
                if pnl > 0:
                    profitable_count += 1
                else:
                    break
            self.consecutive_profitable_days = profitable_count
            
            ready = sharpe > 1.0 and self.consecutive_profitable_days >= 30 and len(self.daily_pnl) >= 30
            
            if ready:
                reason = f"Sharpe={sharpe:.2f} > 1.0, {self.consecutive_profitable_days} profitable days"
            else:
                reasons = []
                if sharpe <= 1.0:
                    reasons.append(f"Sharpe={sharpe:.2f} needs > 1.0")
                if self.consecutive_profitable_days < 30:
                    reasons.append(f"Only {self.consecutive_profitable_days} profitable days, need 30")
                reason = "; ".join(reasons) if reasons else "Insufficient data"
            
            return GateStatus(
                mode=self.mode,
                sharpe_ratio=float(sharpe),
                consecutive_profitable_days=self.consecutive_profitable_days,
                ready_for_real=ready,
                reason=reason,
            )
        except Exception as e:
            logger.error(f"Evaluation failed: {e}")
            return GateStatus(
                mode=self.mode,
                sharpe_ratio=0.0,
                consecutive_profitable_days=self.consecutive_profitable_days,
                ready_for_real=False,
                reason=f"Evaluation error: {e}",
            )
    
    def record_trade(self, pnl: float):
        """Record a paper trade result."""
        try:
            self.daily_pnl.append(pnl)
            logger.info(f"Paper trade recorded: PnL={pnl:.4f}")
        except Exception as e:
            logger.error(f"Failed to record trade: {e}")
    
    def switch_to_real(self, approved: bool = False) -> bool:
        """
        Attempt to switch to real trading mode.
        Requires explicit approval.
        """
        try:
            status = self.evaluate()
            
            if not status.ready_for_real:
                logger.warning(f"Cannot switch to real: {status.reason}")
                return False
            
            if not approved:
                logger.info("Real trading requires human approval. No approval given.")
                return False
            
            self.mode = 'real'
            logger.info("SWITCHED TO REAL TRADING MODE")
            return True
        except Exception as e:
            logger.error(f"Switch to real failed: {e}")
            return False
    
    def reset(self):
        """Reset to paper mode."""
        try:
            self.mode = 'paper'
            self.performance_history = []
            self.daily_pnl = []
            self.consecutive_profitable_days = 0
            logger.info("Reset to paper trading mode")
        except Exception as e:
            logger.error(f"Reset failed: {e}")
