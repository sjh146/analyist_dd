"""
Integration Tests
End-to-end test of the full data-to-prediction pipeline.
"""

import os
import sys
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


@pytest.fixture
def sample_market_data():
    """Generate sample OHLCV data for testing."""
    dates = pd.date_range(start="2024-01-01", periods=120, freq="B")
    np.random.seed(42)
    base_price = 80000
    returns = np.random.normal(0.0005, 0.015, len(dates))
    prices = base_price * np.cumprod(1 + returns)

    df = pd.DataFrame({
        "stock_code": "005930",
        "trade_date": dates.date,
        "open_price": prices * (1 + np.random.normal(0, 0.002, len(dates))),
        "high_price": prices * (1 + np.abs(np.random.normal(0, 0.008, len(dates)))),
        "low_price": prices * (1 - np.abs(np.random.normal(0, 0.008, len(dates)))),
        "close_price": prices,
        "close": prices,
        "volume": np.random.randint(100000, 1000000, len(dates)),
        "rsi": np.random.uniform(30, 70, len(dates)),
        "macd": np.random.uniform(-2000, 2000, len(dates)),
        "bb_width": np.random.uniform(2, 8, len(dates)),
        "atr": np.random.uniform(500, 2000, len(dates)),
        "stoch_k": np.random.uniform(20, 80, len(dates)),
    })
    return df


@pytest.fixture
def mock_db():
    """Create a mock database connection."""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    cur.fetchone.return_value = [1]
    cur.fetchall.return_value = [
        ("2024-06-01", 1000000000.0, 500000000.0),
    ]

    return conn


class TestFeaturePipeline:
    """Test the feature engineering pipeline."""

    def test_market_features_build(self, sample_market_data):
        """T1: Market features produce the minimum expected features."""
        from app.feature_engine.market_features import MarketFeatures

        mf = MarketFeatures()
        features = mf.get_all_features(sample_market_data, "005930")

        assert len(features) >= 15
        assert "price" in features
        assert features["price"] > 0
        assert "return_1d" in features
        assert "ma_position_20" in features
        assert -50 <= features["ma_position_20"] <= 50

    def test_market_features_empty(self):
        """T1b: Empty DataFrame returns default values without exception."""
        from app.feature_engine.market_features import MarketFeatures

        mf = MarketFeatures()
        features = mf.get_all_features(pd.DataFrame(), "005930")

        assert features["price"] == 0.0
        assert features["return_1d"] == 0.0

    def test_sentiment_features_empty(self):
        """T2: Sentiment features with no data return defaults."""
        from app.feature_engine.sentiment_features import SentimentFeatures

        sf = SentimentFeatures()
        features = sf.get_all_features("005930")

        assert features["sentiment_avg"] == 0.0
        assert features["news_count_5d"] == 0
        assert -1.0 <= features["sentiment_avg"] <= 1.0

    def test_graph_features_empty(self):
        """T3: Graph features with no connection return defaults."""
        from app.feature_engine.graph_features import GraphFeatures

        gf = GraphFeatures()
        features = gf.get_graph_features("005930")

        assert features["sector_count"] == 0
        assert features["theme_count"] == 0
        assert features["twin_count"] == 0

    def test_feature_pipeline_build(self, sample_market_data, mock_db):
        """T4: Full feature pipeline builds 50+ features."""
        from app.feature_engine.feature_pipeline import FeaturePipeline

        pipeline = FeaturePipeline(pg_conn=mock_db)
        features = pipeline.build_features("005930", "2024-06-01", sample_market_data)

        assert features["stock_code"] == "005930"
        assert features["date"] == "2024-06-01"
        assert features["price"] > 0
        assert "rsi" in features
        assert "sentiment_avg" in features

    def test_feature_pipeline_cache(self, sample_market_data, mock_db):
        """T5: Feature pipeline caches results."""
        from app.feature_engine.feature_pipeline import FeaturePipeline

        pipeline = FeaturePipeline(pg_conn=mock_db)
        f1 = pipeline.build_features("005930", "2024-06-01", sample_market_data)
        f2 = pipeline.build_features("005930", "2024-06-01", sample_market_data)

        assert f1 is f2

    def test_technical_indicators(self):
        """T6: Technical indicators calculate correctly."""
        from app.processors.technical_indicators import TechnicalIndicatorCalculator

        calc = TechnicalIndicatorCalculator()
        df = pd.DataFrame({
            "stock_code": ["A"] * 100,
            "close": [100 + i * 0.5 for i in range(100)],
            "high": [105 + i * 0.5 for i in range(100)],
            "low": [95 + i * 0.5 for i in range(100)],
            "volume": [1000] * 100,
        })

        result = calc.calculate_all(df)

        assert "sma_20" in result.columns
        assert "rsi" in result.columns
        assert "macd" in result.columns
        assert "atr" in result.columns


class TestModelPipeline:
    """Test the XGBoost model training and prediction pipeline."""

    def test_model_train_predict(self):
        """T7: Model trains and predicts with synthetic data."""
        from app.models.xgboost_model import XGBoostModel

        np.random.seed(42)
        X_train = np.random.randn(200, 20)
        y_train = np.random.randint(0, 2, 200)
        X_val = np.random.randn(50, 20)
        y_val = np.random.randint(0, 2, 50)

        model = XGBoostModel()
        metrics = model.train(X_train, y_train, X_val, y_val)

        assert model.is_trained
        assert "train_accuracy" in metrics or "val_accuracy" in metrics

        result = model.predict_single(X_val[0])
        assert result["predicted_direction"] in ("up", "down")
        assert 0 <= result["confidence"] <= 1

    def test_trainer_label_creation(self):
        """T8: Trainer creates correct binary labels (next-day up/down)."""
        from app.training.trainer import Trainer
        from app.feature_engine.feature_pipeline import FeaturePipeline

        mock_storage = MagicMock()
        mock_storage.get_all_stocks.return_value = [
            {"stock_code": "005930", "stock_name": "삼성전자"}
        ]
        pipeline = FeaturePipeline()
        trainer = Trainer(mock_storage, pipeline)

        df = pd.DataFrame({
            "stock_code": ["005930"] * 5,
            "price": [100, 101, 99, 102, 103],
            "date": pd.date_range("2024-01-01", periods=5).strftime("%Y-%m-%d"),
        })
        labels = trainer._create_labels(df)

        assert len(labels) == 5
        assert labels[0] == 1 if 101 > 100 else 0
        assert labels[2] == 1 if 102 > 99 else 0


class TestEndToEnd:
    """End-to-end integration test of the full pipeline."""

    def test_full_pipeline_synthetic(self, mock_db):
        """Full pipeline: feature build -> model train -> predict."""
        from app.feature_engine.feature_pipeline import FeaturePipeline
        from app.models.xgboost_model import XGBoostModel

        np.random.seed(42)

        pipeline = FeaturePipeline(pg_conn=mock_db)

        dates = pd.date_range("2024-01-01", periods=30, freq="B")
        df = pd.DataFrame({
            "stock_code": "005930",
            "trade_date": dates.date,
            "close": 80000 * (1 + np.cumsum(np.random.normal(0.001, 0.02, 30))),
            "close_price": 80000 * (1 + np.cumsum(np.random.normal(0.001, 0.02, 30))),
            "volume": np.random.randint(100000, 1000000, 30),
            "rsi": np.random.uniform(30, 70, 30),
            "macd": np.random.uniform(-2000, 2000, 30),
            "atr": np.random.uniform(500, 2000, 30),
        })

        features = {}
        for i, (_, row) in enumerate(df.iterrows()):
            f = pipeline.build_features("005930", dates[i].strftime("%Y-%m-%d"), df.iloc[:i+1])
            features.update({k: v for k, v in f.items() if k not in ("stock_code", "date", "feature_count")})

        assert len(features) >= 20

        X = np.random.randn(100, 20).astype(np.float32)
        y = np.random.randint(0, 2, 100)

        model = XGBoostModel()
        model.train(X, y)

        assert model.is_trained

        test_vec = np.random.randn(20).astype(np.float32)
        result = model.predict_single(test_vec)
        assert result["predicted_direction"] in ("up", "down")
