#!/usr/bin/env python3
"""
E2E Pipeline Test Script — validates the full v2 pipeline with mock data.
Runs OUTSIDE Docker (directly on the host). NOT a pytest file.

Usage:
    python tests/test_e2e_pipeline.py
"""

import sys
import os
from pathlib import Path

# ── Handle imports from the project modules ──────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Add service parent directories to sys.path
_service_parents = [
    PROJECT_ROOT / "services" / "news-analyzer",
    PROJECT_ROOT / "services" / "xgboost-ml",
    PROJECT_ROOT / "services" / "backtester",
    PROJECT_ROOT / "services" / "shared",
    PROJECT_ROOT / "services" / "strategy_agents",
    PROJECT_ROOT / "services" / "stock-vectorizer",
    PROJECT_ROOT / "services",
    PROJECT_ROOT,
]

for d in _service_parents:
    if d.exists() and str(d) not in sys.path:
        sys.path.insert(0, str(d))

# ── Imports ───────────────────────────────────────────────────────────────
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

np.random.seed(42)


# ── Helper: manage app package conflicts ─────────────────────────────────

def _use_news_analyzer_app():
    """Ensure news-analyzer's 'app' package is used. Clears cached app modules."""
    _clear_app_modules()
    na_path = str(PROJECT_ROOT / "services" / "news-analyzer")
    xgb_path = str(PROJECT_ROOT / "services" / "xgboost-ml")
    # Remove both
    for p in [xgb_path, na_path]:
        while p in sys.path:
            sys.path.remove(p)
    # insert(0, ...) pushes previous entries down, so insert in reverse order
    # We want news-analyzer first in path, so insert it LAST
    sys.path.insert(0, xgb_path)
    sys.path.insert(0, na_path)


def _use_xgboost_app():
    """Ensure xgboost-ml's 'app' package is used. Clears cached app modules."""
    _clear_app_modules()
    na_path = str(PROJECT_ROOT / "services" / "news-analyzer")
    xgb_path = str(PROJECT_ROOT / "services" / "xgboost-ml")
    # Remove both
    for p in [xgb_path, na_path]:
        while p in sys.path:
            sys.path.remove(p)
    # We want xgboost-ml first in path, so insert it LAST
    sys.path.insert(0, na_path)
    sys.path.insert(0, xgb_path)


def _clear_app_modules():
    """Remove all 'app' and 'app.*' modules from sys.modules cache."""
    for mod_name in list(sys.modules.keys()):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]


# ── Scenario 1: Data Collection → DeepSeek Analysis → Data Quality ───────

def test_scenario_1():
    """Data Collection → DeepSeek Analysis → Data Quality"""
    _use_news_analyzer_app()

    # Mock openai and tenacity before importing the analyzer.
    # tenacity.retry must be a pass-through decorator so async functions stay async.
    def _passthrough_retry(*args, **kwargs):
        def decorator(f):
            return f
        return decorator

    mock_tenacity = MagicMock()
    mock_tenacity.retry = _passthrough_retry
    mock_tenacity.stop_after_attempt = lambda n: MagicMock()
    mock_tenacity.wait_exponential = lambda **kw: MagicMock()

    with patch.dict(sys.modules, {
        "openai": MagicMock(),
        "tenacity": mock_tenacity,
    }):
        _clear_app_modules()

        from app.models.schemas import Article, AnalysisResult
        from app.analyzers.deepseek_analyzer import DeepSeekAnalyzer
        from app.data_quality.range_rule import RangeRule
        from app.data_quality.validator import Validator

    # Create mock article
    article = Article(
        source="test",
        title="Test Article for E2E Pipeline",
        content="This is a test article content for the end-to-end pipeline validation.",
        url="https://example.com/test-article",
        published_at=datetime.now(),
    )

    # Create DeepSeekAnalyzer with _simulate=True (no API key)
    analyzer = DeepSeekAnalyzer(api_key="")
    assert analyzer._simulate is True, "Analyzer should be in simulate mode"

    # Run analysis
    import asyncio
    result = asyncio.run(analyzer.analyze_article(article))

    # Assert AnalysisResult has expected fields
    assert isinstance(result, AnalysisResult), "Result should be AnalysisResult"
    assert hasattr(result, "authenticity_score"), "Missing authenticity_score"
    assert hasattr(result, "sentiment_score"), "Missing sentiment_score"
    assert hasattr(result, "confidence"), "Missing confidence"
    assert hasattr(result, "authenticity_label"), "Missing authenticity_label"
    assert hasattr(result, "sentiment_label"), "Missing sentiment_label"
    assert 0.0 <= result.authenticity_score <= 1.0, "authenticity_score out of range"
    assert -1.0 <= result.sentiment_score <= 1.0, "sentiment_score out of range"

    # Push through DataQualityIntegration Validator with RangeRule(-1, 1)
    validator = Validator()
    range_rule = RangeRule(-1.0, 1.0, name="sentiment_score_range")
    validator.add_rule(range_rule)

    # Normal score should pass
    normal_result = validator.validate_value(0.5)
    rule_key = range_rule.description()
    assert normal_result[rule_key] == "pass", f"Normal score should pass, got {normal_result}"

    # Outlier score should fail
    outlier_result = validator.validate_value(5.0)
    assert outlier_result[rule_key] == "fail", f"Outlier score should fail, got {outlier_result}"

    print("  ✓ Scenario 1 passed: Data Collection → DeepSeek Analysis → Data Quality")


# ── Scenario 2: Feature Store save/load ───────────────────────────────────

def test_scenario_2():
    """Feature Store save/load"""
    _use_xgboost_app()

    from app.feature_engine.feature_store import FeatureStore

    # --- Test save + load with separate mocks ---
    # Mock for save_features (uses executemany + commit, no fetchall)
    save_cursor = MagicMock()
    save_conn = MagicMock()
    save_conn.cursor.return_value = save_cursor

    store = FeatureStore(pg_conn=save_conn)

    features = {
        "return_1d": 0.01,
        "return_5d": 0.05,
        "volatility_20d": 0.15,
        "volume_ratio": 1.2,
    }
    save_ok = store.save_features("005930", "2024-01-15", features)
    assert save_ok is True, "save_features should return True"

    # Mock for load_features (uses fetchall)
    load_cursor = MagicMock()
    load_cursor.fetchall.return_value = [
        ("return_1d", 0.01),
        ("return_5d", 0.05),
        ("volatility_20d", 0.15),
    ]
    load_conn = MagicMock()
    load_conn.cursor.return_value = load_cursor

    store2 = FeatureStore(pg_conn=load_conn)
    loaded = store2.load_features("005930", "2024-01-15")
    assert loaded["return_1d"] == 0.01, "return_1d mismatch"
    assert loaded["return_5d"] == 0.05, "return_5d mismatch"
    assert loaded["volatility_20d"] == 0.15, "volatility_20d mismatch"

    # Test batch load returns DataFrame
    batch_cursor = MagicMock()
    batch_cursor.fetchall.return_value = [
        ("005930", "2024-01-15", "return_1d", 0.01),
        ("005930", "2024-01-15", "return_5d", 0.05),
        ("005930", "2024-01-16", "return_1d", 0.02),
    ]
    batch_conn = MagicMock()
    batch_conn.cursor.return_value = batch_cursor

    store3 = FeatureStore(pg_conn=batch_conn)
    batch = store3.load_batch(["005930"], "2024-01-15", "2024-01-16")
    assert isinstance(batch, pd.DataFrame), "load_batch should return DataFrame"
    assert not batch.empty, "DataFrame should not be empty"

    print("  ✓ Scenario 2 passed: Feature Store save/load")


# ── Scenario 3: Feature Pipeline 100+ features ───────────────────────────

def test_scenario_3():
    """Feature Pipeline 100+ features"""
    _use_xgboost_app()

    from app.feature_engine.feature_pipeline import FeaturePipeline
    from app.feature_engine.feature_store import FeatureStore

    # Create mock FeatureStore
    mock_store = MagicMock(spec=FeatureStore)
    mock_store.load_features.return_value = {}  # Force compute path

    # Create FeaturePipeline with mock store
    pipeline = FeaturePipeline(
        pg_conn=None,
        neo4j_conn=None,
        use_feature_store=True,
        feature_store=mock_store,
    )

    # Create sample market data with enough rows for all feature computations
    # Include technical indicator columns so MarketFeatures.get_technical_features() returns them
    n = 120
    dates = pd.date_range(end="2024-01-15", periods=n, freq="B")
    close_prices = 100.0 * (1 + np.random.randn(n).cumsum() * 0.01)
    market_df = pd.DataFrame({
        "close": close_prices,
        "high": close_prices * 1.02,
        "low": close_prices * 0.98,
        "volume": np.random.randint(100000, 1000000, n),
        "open": close_prices * 0.99,
        "rsi": np.random.uniform(30, 70, n),
        "macd": np.random.randn(n) * 0.5,
        "macd_signal": np.random.randn(n) * 0.3,
        "macd_hist": np.random.randn(n) * 0.2,
        "bb_width": np.random.uniform(0.01, 0.05, n),
        "bb_middle": close_prices,
        "atr": np.random.uniform(0.5, 2.0, n),
        "stoch_k": np.random.uniform(20, 80, n),
        "stoch_d": np.random.uniform(20, 80, n),
        "obv": np.random.randint(1000000, 10000000, n),
    }, index=dates)

    # Build features
    features = pipeline.build_features("005930", "2024-01-15", market_df)

    # Assert 100+ feature keys (excluding metadata keys)
    feature_keys = {k for k in features.keys() if k not in ("stock_code", "date", "feature_count")}
    assert len(feature_keys) >= 100, f"Expected 100+ features, got {len(feature_keys)}"

    print(f"  ✓ Scenario 3 passed: Feature Pipeline {len(feature_keys)} features")


# ── Scenario 4: Ensemble Model train/predict ─────────────────────────────

def test_scenario_4():
    """Ensemble Model train/predict"""
    _use_xgboost_app()

    # The xgboost-ml app/models/__init__.py eagerly imports lightgbm, xgboost, catboost
    # which crash with numpy 2.x. Mock them before importing.
    mock_xgb_module = MagicMock()
    mock_lgb_module = MagicMock()
    mock_cat_module = MagicMock()
    mock_joblib = MagicMock()

    with patch.dict(sys.modules, {
        "xgboost": mock_xgb_module,
        "lightgbm": mock_lgb_module,
        "catboost": mock_cat_module,
        "joblib": mock_joblib,
    }):
        # Clear cached app modules so they reimport with mocks
        _clear_app_modules()

        from app.models.ensemble_model import EnsembleModel

        # Generate synthetic 20-feature data
        n_features = 20

        # Create EnsembleModel
        model = EnsembleModel(model_dir="/tmp/e2e_test_models")

        # Patch the sub-models with MagicMock
        mock_xgb = MagicMock()
        mock_xgb.predict_single.return_value = {
            "predicted_probability": 0.65,
            "predicted_direction": "up",
            "confidence": 0.3,
        }
        mock_lgb = MagicMock()
        mock_lgb.predict_single.return_value = {
            "predicted_probability": 0.55,
            "predicted_direction": "up",
            "confidence": 0.1,
        }
        mock_cat = MagicMock()
        mock_cat.predict_single.return_value = {
            "predicted_probability": 0.72,
            "predicted_direction": "up",
            "confidence": 0.44,
        }

        model.models = [mock_xgb, mock_lgb, mock_cat]
        model._is_trained = True

        # Run predict_single
        features = np.random.randn(n_features).astype(np.float32)
        result = model.predict_single(features)

        # Assert ensemble key with direction and confidence
        assert "ensemble" in result, "Missing ensemble key"
        assert result["ensemble"]["direction"] in ("up", "down"), "Invalid direction"
        assert 0.0 <= result["ensemble"]["confidence"] <= 1.0, "Confidence out of range"

        # Assert all 3 models contributed
        assert result["model_count"] == 3, f"Expected 3 models, got {result['model_count']}"

    print("  ✓ Scenario 4 passed: Ensemble Model train/predict")


# ── Scenario 5: Redis Streams signal flow ────────────────────────────────

def test_scenario_5():
    """Redis Streams signal flow"""
    from services.shared.redis_streams import RedisStreams

    # Create RedisStreams with MagicMock client
    mock_client = MagicMock()
    mock_client.xadd.return_value = "1680000000000-0"
    mock_client.xread.return_value = [
        [
            b"trading:signals",
            [(b"1680000000000-0", {b"stock_code": b"005930", b"signal": b"buy"})],
        ]
    ]
    mock_client.xreadgroup.return_value = [
        [
            b"trading:signals",
            [(b"1680000000000-0", {b"stock_code": b"005930", b"signal": b"buy"})],
        ]
    ]
    mock_client.xack.return_value = 1
    mock_client.xgroup_create.return_value = True

    streams = RedisStreams()
    streams._client = mock_client
    streams._pool = MagicMock()

    # Xadd a signal
    msg_id = streams.xadd("trading:signals", {"stock_code": "005930", "signal": "buy"})
    assert msg_id == "1680000000000-0", "xadd should return message ID"

    # Xread it back
    read_result = streams.xread({"trading:signals": ">"}, block=1000)
    assert len(read_result) > 0, "xread should return messages"
    assert read_result[0][0] == b"trading:signals", "Stream name mismatch"

    # Create consumer group
    group_ok = streams.create_group("trading:signals", "signal_consumers")
    assert group_ok is True, "create_group should succeed"

    # Xreadgroup + xack
    group_result = streams.xreadgroup("signal_consumers", "worker1", {"trading:signals": ">"}, block=1000)
    assert len(group_result) > 0, "xreadgroup should return messages"

    ack_count = streams.xack("trading:signals", "signal_consumers", "1680000000000-0")
    assert ack_count == 1, "xack should acknowledge 1 message"

    print("  ✓ Scenario 5 passed: Redis Streams signal flow")


# ── Scenario 6: Predictor → Streams integration ──────────────────────────

def test_scenario_6():
    """Predictor → Streams integration"""
    _use_xgboost_app()

    # The predictor imports redis and services.shared.redis_streams.
    # Mock redis to avoid connection attempts.
    mock_redis_module = MagicMock()
    mock_redis_module.Redis = MagicMock

    with patch.dict(sys.modules, {
        "redis": mock_redis_module,
    }):
        # Clear cached modules so they reimport with mocks
        _clear_app_modules()
        for mod_name in list(sys.modules.keys()):
            if "redis_streams" in mod_name:
                del sys.modules[mod_name]

        from app.inference.predictor import Predictor

        # Create mocks
        mock_storage = MagicMock()
        mock_storage.get_all_stocks.return_value = [
            {"stock_code": "005930", "stock_name": "Samsung"},
            {"stock_code": "000660", "stock_name": "SK Hynix"},
            {"stock_code": "035420", "stock_name": "Naver"},
            {"stock_code": "005380", "stock_name": "Hyundai"},
            {"stock_code": "051910", "stock_name": "LG Chem"},
            {"stock_code": "006400", "stock_name": "Kakao"},
            {"stock_code": "035720", "stock_name": "Kakao"},
            {"stock_code": "000270", "stock_name": "Kia"},
            {"stock_code": "068270", "stock_name": "Celltrion"},
            {"stock_code": "105560", "stock_name": "KB Financial"},
            {"stock_code": "055550", "stock_name": "Shinhan"},
            {"stock_code": "012330", "stock_name": "Hyundai Mobis"},
        ]

        mock_feature_pipeline = MagicMock()
        mock_feature_pipeline.build_features.return_value = {
            "return_1d": 0.01, "return_5d": 0.05, "volatility_20d": 0.15,
        }
        mock_feature_pipeline.get_feature_names.return_value = [
            "return_1d", "return_5d", "volatility_20d",
        ]

        mock_model = MagicMock()
        # Simulate varying confidence levels to test filtering
        mock_model.predict_single.side_effect = [
            {"predicted_probability": 0.85, "predicted_direction": "up", "confidence": 0.7},
            {"predicted_probability": 0.92, "predicted_direction": "up", "confidence": 0.84},
            {"predicted_probability": 0.55, "predicted_direction": "up", "confidence": 0.1},
            {"predicted_probability": 0.78, "predicted_direction": "down", "confidence": 0.56},
            {"predicted_probability": 0.95, "predicted_direction": "up", "confidence": 0.9},
            {"predicted_probability": 0.65, "predicted_direction": "down", "confidence": 0.3},
            {"predicted_probability": 0.88, "predicted_direction": "up", "confidence": 0.76},
            {"predicted_probability": 0.72, "predicted_direction": "down", "confidence": 0.44},
            {"predicted_probability": 0.91, "predicted_direction": "up", "confidence": 0.82},
            {"predicted_probability": 0.60, "predicted_direction": "down", "confidence": 0.2},
            {"predicted_probability": 0.83, "predicted_direction": "up", "confidence": 0.66},
            {"predicted_probability": 0.50, "predicted_direction": "down", "confidence": 0.0},
        ]

        mock_redis_streams = MagicMock()
        mock_redis_streams.xadd.return_value = "mock-id"

        predictor = Predictor(
            storage=mock_storage,
            feature_pipeline=mock_feature_pipeline,
            model=mock_model,
            redis_client=None,
        )
        predictor._streams = mock_redis_streams

        # Run predict_all
        predictions = predictor.predict_all()
        assert isinstance(predictions, list), "predict_all should return list"
        assert len(predictions) == 12, f"Expected 12 predictions, got {len(predictions)}"

        # Run publish_signals_to_redis
        predictor.publish_signals_to_redis(predictions)

        # Assert confidence filter (>= 0.6) and top-10 limit
        expected_signal_count = sum(1 for p in predictions if p["confidence"] >= 0.6)
        assert expected_signal_count == 6, f"Expected 6 signals >= 0.6 confidence, got {expected_signal_count}"
        assert mock_redis_streams.xadd.call_count == expected_signal_count, (
            f"Expected {expected_signal_count} xadd calls, got {mock_redis_streams.xadd.call_count}"
        )

    print("  ✓ Scenario 6 passed: Predictor → Streams integration")


# ── Scenario 7: Monte Carlo Backtesting ──────────────────────────────────

def test_scenario_7():
    """Monte Carlo Backtesting"""
    from services.backtester.monte_carlo import MonteCarloEngine

    engine = MonteCarloEngine(db_connection=None)

    # Run simulation with 1000 sims (uses synthetic data since db=None)
    result = engine.run_simulation(
        stock_code="005930",
        lookback_days=252,
        n_simulations=1000,
        confidence_level=0.95,
        risk_free_rate=0.02,
    )

    # Assert result contains expected metrics
    assert hasattr(result, "var_99"), "Missing VaR (99%)"
    assert hasattr(result, "cvar_95"), "Missing CVaR"
    assert hasattr(result, "sharpe_ratio"), "Missing Sharpe ratio"
    assert hasattr(result, "max_drawdown"), "Missing max_drawdown"
    assert hasattr(result, "var_95"), "Missing VaR (95%)"
    assert hasattr(result, "sortino_ratio"), "Missing Sortino ratio"
    assert hasattr(result, "win_rate"), "Missing win_rate"
    assert hasattr(result, "volatility"), "Missing volatility"

    # VaR < 0 (negative returns at tail)
    assert result.var_99 < 0, f"VaR(99%) should be negative, got {result.var_99}"
    assert result.var_95 < 0, f"VaR(95%) should be negative, got {result.var_95}"

    # Sharpe is finite
    assert np.isfinite(result.sharpe_ratio), "Sharpe ratio should be finite"

    # CVaR should be <= VaR (more extreme)
    assert result.cvar_95 <= result.var_95, "CVaR should be <= VaR"

    print("  ✓ Scenario 7 passed: Monte Carlo Backtesting")


# ── Scenario 8: Paper Trading Gate ───────────────────────────────────────

def test_scenario_8():
    """Paper Trading Gate"""
    from services.backtester.paper_trading import PaperTradingGate

    gate = PaperTradingGate()

    # Assert mode is 'paper'
    assert gate.mode == "paper", "Gate should start in paper mode"

    # Record 5 trades with mixed PnL
    trades = [0.02, -0.01, 0.03, -0.015, 0.01]
    for pnl in trades:
        gate.record_trade(pnl)

    assert len(gate.daily_pnl) == 5, f"Expected 5 trades, got {len(gate.daily_pnl)}"

    # Run evaluate()
    status = gate.evaluate()

    # Assert metrics dict returned
    assert hasattr(status, "mode"), "Missing mode"
    assert hasattr(status, "sharpe_ratio"), "Missing sharpe_ratio"
    assert hasattr(status, "consecutive_profitable_days"), "Missing consecutive_profitable_days"
    assert hasattr(status, "ready_for_real"), "Missing ready_for_real"
    assert hasattr(status, "reason"), "Missing reason"

    # Assert ready_for_real is False (mixed PnL + < 30 days)
    assert status.ready_for_real is False, "Should not be ready for real trading"
    assert status.mode == "paper", "Mode should remain paper"

    print("  ✓ Scenario 8 passed: Paper Trading Gate")


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scenarios = [
        test_scenario_1,
        test_scenario_2,
        test_scenario_3,
        test_scenario_4,
        test_scenario_5,
        test_scenario_6,
        test_scenario_7,
        test_scenario_8,
    ]
    passed = 0
    for s in scenarios:
        try:
            s()
            passed += 1
        except Exception as e:
            import traceback
            print(f"  ✗ {s.__name__} FAILED: {e}")
            traceback.print_exc()

    print(f"\n{'='*40}")
    print(f"E2E Result: {passed}/{len(scenarios)} passed")
    exit(0 if passed == len(scenarios) else 1)
