"""
factors - 공용 팩터 모듈 (강환국『하면 된다! 퀀트투자』 규칙 명세 구현)

Public API:
- factor_base.rank_scores / zscore_scores / normalize_rank_confidence
- financial_snapshot.FinancialSnapshot (point-in-time 재무 스냅샷)
- universe.filter_universe (시총/거래대금/재무/상장기간 유니버스 필터)
- value_ratios.compute_ratios (PER/PBR/ROE/PSR/GPA)
- factor_scores.value_scores / quality_scores / momentum_scores / lowvol_scores

All factor computations are point-in-time: financial rows are restricted to
report_date <= asof_date and price series are restricted to trade_date <=
asof_date, so no look-ahead bias enters factor scores or backtests.
"""

from app.factors.factor_base import (
    FactorBase,
    normalize_rank_confidence,
    rank_scores,
    zscore_scores,
)
from app.factors.financial_snapshot import FinancialSnapshot

__all__ = [
    "FactorBase",
    "FinancialSnapshot",
    "normalize_rank_confidence",
    "rank_scores",
    "zscore_scores",
]
