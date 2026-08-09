"""Unit tests for FinancialSnapshot point-in-time behavior."""

import pytest

from app.factors.financial_snapshot import FinancialSnapshot


class MockStorage:
    """Storage stub: returns fixed financial rows, honoring asof_date filter."""

    def __init__(self, rows):
        self.rows = rows

    def get_financial_statements(self, stock_code, asof_date=None):
        out = [r for r in self.rows if r["stock_code"] == stock_code]
        if asof_date is not None:
            out = [r for r in out if str(r["report_date"]) <= str(asof_date)]
        return sorted(out, key=lambda r: str(r["report_date"]))


def make_row(stock_code, report_date, revenue=1000):
    return {
        "stock_code": stock_code,
        "report_date": report_date,
        "revenue": revenue,
        "net_income": revenue * 0.1,
    }


ROWS = [
    make_row("000001", "2024-03-31", 1000),
    make_row("000001", "2024-06-30", 1200),
    make_row("000001", "2024-09-30", 1400),
    make_row("000002", "2024-06-30", 999),
]


class TestFinancialSnapshotHappy:
    def test_latest_excludes_future_report_date(self):
        snap = FinancialSnapshot(MockStorage(ROWS))
        latest = snap.get_latest("000001", asof_date="2024-06-30")
        assert latest["report_date"] == "2024-06-30"

    def test_latest_without_asof_returns_newest(self):
        snap = FinancialSnapshot(MockStorage(ROWS))
        latest = snap.get_latest("000001")
        assert latest["report_date"] == "2024-09-30"

    def test_history_returns_quarterly_rows_ascending(self):
        snap = FinancialSnapshot(MockStorage(ROWS))
        history = snap.get_history("000001", asof_date="2024-06-30")
        assert [r["report_date"] for r in history] == ["2024-03-31", "2024-06-30"]

    def test_history_truncated(self):
        snap = FinancialSnapshot(MockStorage(ROWS))
        history = snap.get_history("000001", asof_date=None, n_quarters=2)
        assert [r["report_date"] for r in history] == ["2024-06-30", "2024-09-30"]

    def test_has_financial_data_true(self):
        snap = FinancialSnapshot(MockStorage(ROWS))
        assert snap.has_financial_data("000001", asof_date="2024-06-30") is True


class TestFinancialSnapshotFailure:
    def test_asof_before_all_reports_returns_empty(self):
        snap = FinancialSnapshot(MockStorage(ROWS))
        assert snap.get_latest("000001", asof_date="2023-12-31") is None

    def test_unknown_stock_returns_empty(self):
        snap = FinancialSnapshot(MockStorage(ROWS))
        assert snap.get_latest("999999") is None
        assert snap.has_financial_data("999999") is False

    def test_empty_rows_no_error(self):
        snap = FinancialSnapshot(MockStorage([]))
        assert snap.get_latest("000001", asof_date="2024-06-30") is None
        assert snap.get_history("000001") == []
