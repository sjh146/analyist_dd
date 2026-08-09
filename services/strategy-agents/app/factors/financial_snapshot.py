"""
financial_snapshot - point-in-time 재무 스냅샷.

report_date <= asof_date 인 재무 행만 사용해 미래 보고서를 배제한다
(선견 편향 방지, plan 심층분석 1·3). 공시 지연(분기 종료 후 45일) 추가
반영은 후속 개선 항목으로 문서화되어 있다.
"""

from typing import Dict, List, Optional


class FinancialSnapshot:
    """Point-in-time access to financial_statements via the storage layer."""

    def __init__(self, storage):
        self.storage = storage

    def get_latest(self, stock_code: str, asof_date=None) -> Optional[Dict]:
        """Latest financial row with report_date <= asof_date (None = all-time)."""
        rows = self.storage.get_financial_statements(stock_code, asof_date)
        return rows[-1] if rows else None

    def get_history(self, stock_code: str, asof_date=None, n_quarters: Optional[int] = None) -> List[Dict]:
        """Ascending quarterly history, optionally truncated to the last n rows."""
        rows = self.storage.get_financial_statements(stock_code, asof_date)
        return rows[-n_quarters:] if n_quarters else rows

    def has_financial_data(self, stock_code: str, asof_date=None) -> bool:
        """True if at least one financial row is disclosed by asof_date."""
        return self.get_latest(stock_code, asof_date) is not None
