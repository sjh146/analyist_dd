"""Tests for DART financial collector extension (gross_profit, operating_cash_flow)."""

import pytest

from app.collectors.financial_collector import FinancialCollector


def make_dart_item(stock_code, report_date, account_name, value):
    return {
        "stock_code": stock_code,
        "report_date": report_date,
        "metric": account_name,
        "value": float(value),
        "unit": "KRW",
    }


def to_short_items(dart_items):
    """Mirror the mapping step collect_financials() performs before aggregation."""
    out = []
    for item in dart_items:
        short_name = FinancialCollector.FINANCIAL_METRICS_MAP.get(item["metric"])
        if short_name:
            out.append({**item, "metric": short_name})
    return out


class TestFinancialMetricsMapExtension:
    def test_gross_profit_mapping_present(self):
        assert "ifrs-full_GrossProfit" in FinancialCollector.FINANCIAL_METRICS_MAP
        assert FinancialCollector.FINANCIAL_METRICS_MAP["ifrs-full_GrossProfit"] == "gross_profit"

    def test_operating_cash_flow_mapping_present(self):
        assert "ifrs-full_CashFlowsFromUsedInOperatingActivities" in FinancialCollector.FINANCIAL_METRICS_MAP
        assert (
            FinancialCollector.FINANCIAL_METRICS_MAP[
                "ifrs-full_CashFlowsFromUsedInOperatingActivities"
            ]
            == "operating_cash_flow"
        )

    def test_existing_mappings_untouched(self):
        assert FinancialCollector.FINANCIAL_METRICS_MAP["ifrs-full_Revenue"] == "revenue"
        assert FinancialCollector.FINANCIAL_METRICS_MAP["ifrs-full_Equity"] == "total_equity"


class TestAggregateHappy:
    def test_gross_profit_aggregated_from_dart_name(self):
        collector = FinancialCollector(api_key="your_test_key")
        dart_items = [
            make_dart_item("005930", "2024-06-30", "ifrs-full_GrossProfit", 15000000000000),
            make_dart_item("005930", "2024-06-30", "ifrs-full_Revenue", 75000000000000),
        ]
        result = collector.aggregate_to_financials(to_short_items(dart_items))
        assert result["gross_profit"] == 15000000000000.0
        assert result["revenue"] == 75000000000000.0
        assert result["report_date"] == "2024-06-30"

    def test_operating_cash_flow_aggregated_from_dart_name(self):
        collector = FinancialCollector(api_key="your_test_key")
        dart_items = [
            make_dart_item("005930", "2024-03-31", "ifrs-full_CashFlowsFromUsedInOperatingActivities", 9000000000000),
            make_dart_item("005930", "2024-03-31", "ifrs-full_ProfitLoss", 5000000000000),
        ]
        result = collector.aggregate_to_financials(to_short_items(dart_items))
        assert result["operating_cash_flow"] == 9000000000000.0
        assert result["net_income"] == 5000000000000.0


class TestAggregateFailure:
    def test_missing_gross_profit_key_no_error(self):
        collector = FinancialCollector(api_key="your_test_key")
        dart_items = [make_dart_item("005930", "2024-06-30", "ifrs-full_Revenue", 100)]
        result = collector.aggregate_to_financials(to_short_items(dart_items))
        assert "gross_profit" not in result
        assert result["revenue"] == 100.0

    def test_unknown_dart_account_skipped_no_error(self):
        collector = FinancialCollector(api_key="your_test_key")
        dart_items = [
            make_dart_item("005930", "2024-06-30", "ifrs-full_NotAMetric", 1),
            make_dart_item("005930", "2024-06-30", "ifrs-full_Revenue", 200),
        ]
        mapped = to_short_items(dart_items)
        assert len(mapped) == 1
        result = collector.aggregate_to_financials(mapped)
        assert result["revenue"] == 200.0

    def test_empty_raw_no_error(self):
        collector = FinancialCollector(api_key="your_test_key")
        assert collector.aggregate_to_financials([]) == {}
        assert collector.aggregate_to_financials_history([]) == []


class TestHistoryPreservation:
    def test_history_keeps_every_report_date(self):
        collector = FinancialCollector(api_key="your_test_key")
        dart_items = [
            make_dart_item("005930", "2024-03-31", "ifrs-full_GrossProfit", 10),
            make_dart_item("005930", "2024-06-30", "ifrs-full_GrossProfit", 20),
            make_dart_item("005930", "2024-09-30", "ifrs-full_GrossProfit", 30),
            make_dart_item("005930", "2024-03-31", "ifrs-full_Revenue", 100),
        ]
        rows = collector.aggregate_to_financials_history(to_short_items(dart_items))
        assert len(rows) == 3
        assert [r["report_date"] for r in rows] == ["2024-03-31", "2024-06-30", "2024-09-30"]
        assert rows[0]["gross_profit"] == 10
        assert rows[0]["revenue"] == 100
        assert rows[2]["gross_profit"] == 30

    def test_latest_only_aggregator_unchanged(self):
        collector = FinancialCollector(api_key="your_test_key")
        dart_items = [
            make_dart_item("005930", "2024-03-31", "ifrs-full_GrossProfit", 10),
            make_dart_item("005930", "2024-09-30", "ifrs-full_GrossProfit", 30),
        ]
        result = collector.aggregate_to_financials(to_short_items(dart_items))
        assert result["report_date"] == "2024-09-30"
        assert result["gross_profit"] == 30
