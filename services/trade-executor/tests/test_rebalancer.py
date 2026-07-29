import math
import pytest
from portfolio.rebalancer import RebalancingEngine


class TestCalculateTargetWeights:
    def test_basic_allocation(self):
        engine = RebalancingEngine()
        signals = [
            {"stock_code": "005930", "confidence": 0.8},
            {"stock_code": "000660", "confidence": 0.2},
        ]
        result = engine.calculate_target_weights(signals, risk_budget=1.0)
        assert result["005930"] == pytest.approx(0.8, 5)
        assert result["000660"] == pytest.approx(0.2, 5)

    def test_zero_confidence_equals_weights(self):
        engine = RebalancingEngine()
        signals = [
            {"stock_code": "005930", "confidence": 0.0},
            {"stock_code": "000660", "confidence": 0.0},
        ]
        result = engine.calculate_target_weights(signals, risk_budget=1.0)
        assert result["005930"] == 0.5
        assert result["000660"] == 0.5

    def test_empty_signals(self):
        engine = RebalancingEngine()
        result = engine.calculate_target_weights([])
        assert result == {}

    def test_risk_budget_scaling(self):
        engine = RebalancingEngine()
        signals = [
            {"stock_code": "005930", "confidence": 0.6},
            {"stock_code": "000660", "confidence": 0.4},
        ]
        result = engine.calculate_target_weights(signals, risk_budget=0.5)
        assert result["005930"] == pytest.approx(0.3, 5)
        assert result["000660"] == pytest.approx(0.2, 5)

    def test_clamp_to_max_single(self):
        engine = RebalancingEngine()
        signals = [
            {"stock_code": "005930", "confidence": 0.95},
            {"stock_code": "000660", "confidence": 0.05},
        ]
        result = engine.calculate_target_weights(signals, risk_budget=1.0)
        assert result["005930"] == 0.3  # clamped
        assert result["000660"] == pytest.approx(0.05, 5)

    def test_single_stock(self):
        engine = RebalancingEngine()
        signals = [
            {"stock_code": "005930", "confidence": 1.0},
        ]
        result = engine.calculate_target_weights(signals, risk_budget=1.0)
        assert result["005930"] == 0.3  # clamped

    def test_missing_confidence_defaults_zero(self):
        engine = RebalancingEngine()
        signals = [
            {"stock_code": "005930"},
            {"stock_code": "000660"},
        ]
        result = engine.calculate_target_weights(signals, risk_budget=1.0)
        assert result["005930"] == 0.5
        assert result["000660"] == 0.5


class TestDetectDrift:
    def test_no_drift(self):
        engine = RebalancingEngine()
        current = {"005930": 0.5, "000660": 0.5}
        target = {"005930": 0.5, "000660": 0.5}
        result = engine.detect_drift(current, target, threshold=0.05)
        assert result == []

    def test_drift_detected(self):
        engine = RebalancingEngine()
        current = {"005930": 0.6, "000660": 0.4}
        target = {"005930": 0.5, "000660": 0.5}
        result = engine.detect_drift(current, target, threshold=0.05)
        assert len(result) == 2
        actions = {r["stock_code"]: r["action"] for r in result}
        assert actions["005930"] == "sell"
        assert actions["000660"] == "buy"
        for r in result:
            assert r["drift_pct"] > 0.05

    def test_below_threshold(self):
        engine = RebalancingEngine()
        current = {"005930": 0.51, "000660": 0.49}
        target = {"005930": 0.5, "000660": 0.5}
        result = engine.detect_drift(current, target, threshold=0.05)
        # drift_pct = 0.01/0.5 = 0.02 < 0.05
        assert result == []

    def test_target_zero_stock_with_position(self):
        engine = RebalancingEngine()
        current = {"005930": 0.5}
        target = {}
        result = engine.detect_drift(current, target, threshold=0.05)
        assert len(result) == 1
        assert result[0]["action"] == "sell"
        assert result[0]["drift_pct"] == 1.0

    def test_custom_threshold(self):
        engine = RebalancingEngine()
        current = {"005930": 0.55, "000660": 0.45}
        target = {"005930": 0.5, "000660": 0.5}
        result = engine.detect_drift(current, target, threshold=0.2)
        # drift_pct = 0.05/0.5 = 0.1 < 0.2
        assert result == []

    def test_empty_weights(self):
        engine = RebalancingEngine()
        result = engine.detect_drift({}, {}, threshold=0.05)
        assert result == []

    def test_new_stock_in_target(self):
        engine = RebalancingEngine()
        current = {"005930": 1.0}
        target = {"005930": 0.8, "000660": 0.2}
        result = engine.detect_drift(current, target, threshold=0.05)
        assert len(result) == 2
        actions = {r["stock_code"]: r["action"] for r in result}
        assert actions["005930"] == "sell"
        assert actions["000660"] == "buy"


class TestGenerateRebalanceOrders:
    def test_generates_orders(self):
        engine = RebalancingEngine()
        drift_signals = [
            {
                "stock_code": "005930",
                "current_weight": 0.6,
                "target_weight": 0.5,
                "drift_pct": 0.2,
                "action": "sell",
            },
            {
                "stock_code": "000660",
                "current_weight": 0.4,
                "target_weight": 0.5,
                "drift_pct": 0.25,
                "action": "buy",
            },
        ]
        prices = {"005930": 70000, "000660": 150000}
        result = engine.generate_rebalance_orders(drift_signals, 10000000, prices)
        assert len(result) == 2
        assert result[0]["stock_code"] == "005930"
        assert result[0]["action"] == "sell"
        assert result[1]["stock_code"] == "000660"
        assert result[1]["action"] == "buy"
        # 005930: |0.6 - 0.5| * 10M = 1,000,000 / 70,000 ≈ 14 shares
        assert result[0]["quantity"] == 14
        assert result[0]["dollar_amount"] == 1000000.0

    def test_hold_action_skipped(self):
        engine = RebalancingEngine()
        drift_signals = [
            {
                "stock_code": "005930",
                "current_weight": 0.5,
                "target_weight": 0.5,
                "drift_pct": 0.0,
                "action": "hold",
            },
        ]
        result = engine.generate_rebalance_orders(drift_signals, 10000000)
        assert result == []

    def test_empty_signals(self):
        engine = RebalancingEngine()
        result = engine.generate_rebalance_orders([], 10000000)
        assert result == []

    def test_no_prices_quantity_none(self):
        engine = RebalancingEngine()
        drift_signals = [
            {
                "stock_code": "005930",
                "current_weight": 0.6,
                "target_weight": 0.5,
                "drift_pct": 0.2,
                "action": "sell",
            },
        ]
        result = engine.generate_rebalance_orders(drift_signals, 10000000)
        assert result[0]["quantity"] is None
        assert result[0]["dollar_amount"] == 1000000.0

    def test_zero_portfolio_value(self):
        engine = RebalancingEngine()
        drift_signals = [
            {
                "stock_code": "005930",
                "current_weight": 0.6,
                "target_weight": 0.5,
                "drift_pct": 0.2,
                "action": "sell",
            },
        ]
        result = engine.generate_rebalance_orders(drift_signals, 0)
        assert result[0]["dollar_amount"] == 0.0

    def test_reason_format(self):
        engine = RebalancingEngine()
        drift_signals = [
            {
                "stock_code": "005930",
                "current_weight": 0.6,
                "target_weight": 0.5,
                "drift_pct": 0.2,
                "action": "sell",
            },
        ]
        result = engine.generate_rebalance_orders(drift_signals, 10000000)
        assert "Drift" in result[0]["reason"]
        assert "20.00%" in result[0]["reason"]
