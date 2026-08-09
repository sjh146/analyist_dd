"""Unit tests for value_ratios.compute_ratios (PER/PBR/ROE/PSR/GPA)."""

import pytest
from decimal import Decimal

from app.factors.financial_snapshot import FinancialSnapshot
from app.factors.value_ratios import compute_ratios


class MockStorage:
    """Storage stub: returns fixed financial rows, honoring asof_date filter."""

    def __init__(self, rows):
        self.rows = rows

    def get_financial_statements(self, stock_code, asof_date=None):
        out = [r for r in self.rows if r["stock_code"] == stock_code]
        if asof_date is not None:
            out = [r for r in out if str(r["report_date"]) <= str(asof_date)]
        return sorted(out, key=lambda r: str(r["report_date"]))


def make_row(stock_code, report_date, net_income=1e12, total_equity=1e13,
             revenue=1e13, total_assets=2e13, gross_profit=4e12):
    return {
        "stock_code": stock_code,
        "report_date": report_date,
        "net_income": net_income,
        "total_equity": total_equity,
        "revenue": revenue,
        "total_assets": total_assets,
        "gross_profit": gross_profit,
    }


ROWS = [
    make_row("000001", "2024-06-30"),
    make_row("000002", "2024-06-30"),
]

PIT_ROWS = [
    make_row("000001", "2024-03-31", net_income=1e12),
    make_row("000001", "2024-06-30", net_income=2e12),
]


class TestValueRatiosHappy:
    def test_per_10_from_1e12_income_and_1e13_cap(self):
        ratios = compute_ratios(FinancialSnapshot(MockStorage(ROWS)), {"000001": 1e13})
        assert ratios["000001"]["per"] == pytest.approx(10.0)

    def test_pbr(self):
        ratios = compute_ratios(FinancialSnapshot(MockStorage(ROWS)), {"000001": 1e13})
        assert ratios["000001"]["pbr"] == pytest.approx(1.0)  # 1e13 / 1e13

    def test_roe(self):
        ratios = compute_ratios(FinancialSnapshot(MockStorage(ROWS)), {"000001": 1e13})
        assert ratios["000001"]["roe"] == pytest.approx(0.1)  # 1e12 / 1e13

    def test_psr(self):
        ratios = compute_ratios(FinancialSnapshot(MockStorage(ROWS)), {"000001": 1e13})
        assert ratios["000001"]["psr"] == pytest.approx(1.0)  # 1e13 / 1e13

    def test_gpa(self):
        ratios = compute_ratios(FinancialSnapshot(MockStorage(ROWS)), {"000001": 1e13})
        assert ratios["000001"]["gpa"] == pytest.approx(0.2)  # 4e12 / 2e13

    def test_multiple_stocks(self):
        ratios = compute_ratios(
            FinancialSnapshot(MockStorage(ROWS)), {"000001": 1e13, "000002": 2e13}
        )
        assert ratios["000001"]["per"] == pytest.approx(10.0)
        assert ratios["000002"]["per"] == pytest.approx(20.0)


class TestValueRatiosFailure:
    def test_zero_net_income_per_none(self):
        rows = [make_row("000001", "2024-06-30", net_income=0)]
        ratios = compute_ratios(FinancialSnapshot(MockStorage(rows)), {"000001": 1e13})
        assert ratios["000001"]["per"] is None  # no ZeroDivisionError

    def test_negative_net_income_per_and_roe_none(self):
        rows = [make_row("000001", "2024-06-30", net_income=-5e11)]
        ratios = compute_ratios(FinancialSnapshot(MockStorage(rows)), {"000001": 1e13})
        assert ratios["000001"]["per"] is None
        assert ratios["000001"]["roe"] is None

    def test_zero_equity_pbr_and_roe_none(self):
        rows = [make_row("000001", "2024-06-30", total_equity=0)]
        ratios = compute_ratios(FinancialSnapshot(MockStorage(rows)), {"000001": 1e13})
        assert ratios["000001"]["pbr"] is None
        assert ratios["000001"]["roe"] is None

    def test_missing_gross_profit_gpa_none(self):
        rows = [make_row("000001", "2024-06-30", gross_profit=None)]
        ratios = compute_ratios(FinancialSnapshot(MockStorage(rows)), {"000001": 1e13})
        assert ratios["000001"]["gpa"] is None
        assert ratios["000001"]["per"] == pytest.approx(10.0)  # others unaffected

    def test_missing_market_cap_only_cap_based_none(self):
        ratios = compute_ratios(FinancialSnapshot(MockStorage(ROWS)), {"000001": None})
        r = ratios["000001"]
        assert r["per"] is None and r["pbr"] is None and r["psr"] is None
        assert r["roe"] == pytest.approx(0.1)  # roe needs no market cap

    def test_no_financial_row_all_none(self):
        ratios = compute_ratios(FinancialSnapshot(MockStorage(ROWS)), {"999999": 1e13})
        assert ratios["999999"] == {
            "per": None, "pbr": None, "roe": None, "psr": None, "gpa": None,
        }

    def test_empty_market_cap_map(self):
        assert compute_ratios(FinancialSnapshot(MockStorage(ROWS)), {}) == {}

    def test_decimal_values_coerced(self):
        rows = [{
            "stock_code": "000001",
            "report_date": "2024-06-30",
            "net_income": Decimal("1e12"),
            "total_equity": Decimal("1e13"),
            "revenue": Decimal("1e13"),
            "total_assets": Decimal("2e13"),
            "gross_profit": Decimal("4e12"),
        }]
        ratios = compute_ratios(FinancialSnapshot(MockStorage(rows)), {"000001": Decimal("1e13")})
        assert ratios["000001"]["per"] == pytest.approx(10.0)
        assert ratios["000001"]["roe"] == pytest.approx(0.1)
        assert ratios["000001"]["gpa"] == pytest.approx(0.2)


class TestValueRatiosPointInTime:
    def test_asof_uses_older_report_date(self):
        ratios = compute_ratios(
            FinancialSnapshot(MockStorage(PIT_ROWS)), {"000001": 1e13}, asof_date="2024-03-31"
        )
        assert ratios["000001"]["per"] == pytest.approx(10.0)

    def test_no_asof_uses_newest_row(self):
        ratios = compute_ratios(FinancialSnapshot(MockStorage(PIT_ROWS)), {"000001": 1e13})
        assert ratios["000001"]["per"] == pytest.approx(5.0)  # 1e13 / 2e12
