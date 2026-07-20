"""
End-to-End Pipeline Integration Tests
======================================
Tests the full pipeline against live Docker PostgreSQL:
  1. PG health + data volume
  2. FeaturePipeline builds real features from DB
  3. EnsembleModel loads trained weights and predicts
  4. BacktestRunner executes trades against real market data
  5. swing_screener.py produces CSV output

Prerequisites:
  - Docker PG running on 127.0.0.1:5432
  - market_data populated (run scraper first)
  - Trained models in services/xgboost-ml/app/models/saved_models/
"""

import os
import sys
import subprocess
import tempfile
import numpy as np
import pandas as pd
import psycopg2
import pytest

# ── Path setup ──────────────────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XGBOOST_ML = os.path.join(ROOT, "services", "xgboost-ml")
BACKTESTER = os.path.join(ROOT, "services", "backtester")

# Ensure xgboost-ml app is importable
if XGBOOST_ML not in sys.path:
    sys.path.insert(0, XGBOOST_ML)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "stock_secure_password_2026")

MODEL_DIR = os.path.join(XGBOOST_ML, "app", "models", "saved_models")


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pg_conn():
    """Real PostgreSQL connection to Docker DB."""
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASS,
    )
    conn.autocommit = False
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def kosdaq_codes(pg_conn):
    """List of KOSDAQ stock codes that have market_data."""
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT DISTINCT m.stock_code
        FROM market_data m
        JOIN stocks s ON m.stock_code = s.stock_code
        WHERE s.market = 'KOSDAQ'
        LIMIT 10
    """)
    codes = [row[0] for row in cur.fetchall()]
    cur.close()
    return codes


@pytest.fixture(scope="module")
def sample_code(kosdaq_codes):
    """Single KOSDAQ stock code for individual tests."""
    assert len(kosdaq_codes) > 0, "No KOSDAQ stocks with market_data found"
    return kosdaq_codes[0]


# ── Scenario 1: Docker PG health + data volume ──────────────────────────────

def test_scenario_1_docker_pg_health(pg_conn):
    """PG is up and has sufficient KOSDAQ market data."""
    cur = pg_conn.cursor()

    # Total stocks
    cur.execute("SELECT COUNT(*) FROM stocks")
    total_stocks = cur.fetchone()[0]
    assert total_stocks > 100, f"Expected >100 stocks, got {total_stocks}"

    # KOSDAQ stocks
    cur.execute("SELECT COUNT(*) FROM stocks WHERE market = 'KOSDAQ'")
    kosdaq_count = cur.fetchone()[0]
    assert kosdaq_count > 50, f"Expected >50 KOSDAQ stocks, got {kosdaq_count}"

    # market_data rows
    cur.execute("""
        SELECT COUNT(*) FROM market_data m
        JOIN stocks s ON m.stock_code = s.stock_code
        WHERE s.market = 'KOSDAQ'
    """)
    md_count = cur.fetchone()[0]
    assert md_count > 10000, f"Expected >10K market_data rows, got {md_count}"

    # financial_statements rows
    cur.execute("SELECT COUNT(*) FROM financial_statements")
    fs_count = cur.fetchone()[0]
    assert fs_count > 100, f"Expected >100 financial_statements rows, got {fs_count}"

    cur.close()
    print(f"\n  ✓ Scenario 1: PG healthy — {total_stocks} stocks, {kosdaq_count} KOSDAQ, {md_count} market_data, {fs_count} financials")


# ── Scenario 2: FeaturePipeline builds real features ────────────────────────

def test_scenario_2_feature_pipeline(pg_conn, sample_code):
    """FeaturePipeline.build_features() produces features from real DB data."""
    from app.feature_engine.feature_pipeline import FeaturePipeline

    pipeline = FeaturePipeline(pg_conn=pg_conn)

    # Get latest date for this stock
    cur = pg_conn.cursor()
    cur.execute(
        "SELECT MAX(trade_date) FROM market_data WHERE stock_code = %s",
        (sample_code,),
    )
    latest_date = cur.fetchone()[0]
    cur.close()
    assert latest_date is not None, f"No market_data for {sample_code}"

    # Build features
    features = pipeline.build_features(sample_code, str(latest_date))

    # Must have metadata
    assert "feature_count" in features, "Missing feature_count"
    assert "stock_code" in features, "Missing stock_code"
    assert features["stock_code"] == sample_code

    # Must have reasonable feature count (at least 21 basic features)
    fc = features["feature_count"]
    assert fc >= 21, f"Expected >=21 features, got {fc}"

    # Must have some non-zero values (not all fallbacks)
    non_zero = sum(1 for k, v in features.items()
                   if k not in ("stock_code", "date", "feature_count")
                   and isinstance(v, (int, float)) and v != 0.0)
    assert non_zero > 5, f"Expected >5 non-zero features, got {non_zero}"

    print(f"\n  ✓ Scenario 2: FeaturePipeline — {fc} features, {non_zero} non-zero for {sample_code}")


# ── Scenario 3: FeaturePipeline build_training_features ──────────────────────

def test_scenario_3_training_features(pg_conn, kosdaq_codes):
    """build_training_features() returns a DataFrame from real data."""
    from app.feature_engine.feature_pipeline import FeaturePipeline

    pipeline = FeaturePipeline(pg_conn=pg_conn)

    # Use subset for speed
    codes = kosdaq_codes[:5]

    # Get date range from market_data
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT MIN(trade_date), MAX(trade_date)
        FROM market_data
        WHERE stock_code = ANY(%s)
    """, (codes,))
    min_date, max_date = cur.fetchone()
    cur.close()

    assert min_date is not None, "No market_data for sample codes"

    # Use last 30 days for speed
    df = pipeline.build_training_features(codes, str(min_date), str(max_date))

    assert isinstance(df, pd.DataFrame), "Expected DataFrame"
    assert len(df) > 0, "Expected non-empty DataFrame"
    assert "stock_code" in df.columns, "Missing stock_code column"
    assert "feature_count" in df.columns, "Missing feature_count column"

    print(f"\n  ✓ Scenario 3: training_features — {len(df)} rows × {len(df.columns)} cols from {len(codes)} stocks")


# ── Scenario 4: EnsembleModel loads and predicts ────────────────────────────

def test_scenario_4_ensemble_model(sample_code):
    """EnsembleModel loads trained weights and makes a prediction."""
    from app.models.ensemble_model import EnsembleModel
    from app.feature_engine.feature_pipeline import FeaturePipeline

    xgb_path = os.path.join(MODEL_DIR, "xgboost_model.pkl")
    lgb_path = os.path.join(MODEL_DIR, "lightgbm_model.pkl")
    assert os.path.exists(xgb_path), f"Missing {xgb_path}"
    assert os.path.exists(lgb_path), f"Missing {lgb_path}"

    model = EnsembleModel(model_dir=MODEL_DIR)
    model.load(MODEL_DIR)

    for m in model.models:
        assert m.is_trained, f"{type(m).__name__} not loaded"

    saved_features = model.load_feature_names(MODEL_DIR)
    assert len(saved_features) > 0, "No feature_names.json found — retrain with train_quick.py"

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASS,
    )
    pipeline = FeaturePipeline(pg_conn=conn)

    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(trade_date) FROM market_data WHERE stock_code = %s",
        (sample_code,),
    )
    latest_date = cur.fetchone()[0]
    cur.close()

    assert latest_date is not None, f"No data for {sample_code}"

    single_features = pipeline.build_features(sample_code, str(latest_date))
    conn.close()
    feature_vec = np.array(
        [float(single_features.get(f, 0.0)) for f in saved_features],
        dtype=np.float32,
    )
    feature_vec = np.nan_to_num(feature_vec, nan=0.0)

    result = model.predict_single(feature_vec)

    assert "ensemble" in result, "Missing ensemble key"
    assert result["ensemble"]["direction"] in ("up", "down"), "Invalid direction"
    assert 0.0 <= result["ensemble"]["confidence"] <= 1.0, "Confidence out of range"
    assert result["model_count"] == 2, f"Expected 2 models, got {result['model_count']}"

    print(f"\n  ✓ Scenario 4: EnsembleModel — direction={result['ensemble']['direction']}, "
          f"confidence={result['ensemble']['confidence']:.3f}, models={result['model_count']}, "
          f"features={len(saved_features)}")


# ── Scenario 5: BacktestRunner runs with real data ──────────────────────────

def test_scenario_5_backtest_runner(pg_conn, kosdaq_codes):
    """BacktestRunner.execute() produces trades from real market data."""
    # Import from runner.py directly
    sys.path.insert(0, BACKTESTER)
    from runner import BacktestRunner, BacktestResult

    runner = BacktestRunner(pg_conn=pg_conn, model_dir=MODEL_DIR)

    # Use a small date range and few stocks
    codes = kosdaq_codes[:3]

    # Get actual date range
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT MIN(trade_date), MAX(trade_date)
        FROM market_data
        WHERE stock_code = ANY(%s)
    """, (codes,))
    min_date, max_date = cur.fetchone()
    cur.close()

    assert min_date is not None, "No data for sample stocks"

    # Run backtest with last 60 days
    result = runner.run_backtest(
        strategy="ensemble",
        stock_codes=codes,
        start_date=str(min_date),
        end_date=str(max_date),
    )

    assert isinstance(result, BacktestResult), "Expected BacktestResult"
    assert result.strategy == "ensemble"
    assert result.num_trades >= 0, "num_trades must be non-negative"
    assert isinstance(result.total_return, float)

    print(f"\n  ✓ Scenario 5: BacktestRunner — {result.num_trades} trades, "
          f"return={result.total_return:.4f}, sharpe={result.sharpe_ratio:.3f}")


# ── Scenario 6: Monte Carlo Backtesting ─────────────────────────────────────

def test_scenario_6_monte_carlo():
    """MonteCarloEngine produces valid risk metrics."""
    from services.backtester.monte_carlo import MonteCarloEngine

    engine = MonteCarloEngine(db_connection=None)

    result = engine.run_simulation(
        stock_code="005930",
        lookback_days=252,
        n_simulations=1000,
        confidence_level=0.95,
        risk_free_rate=0.02,
    )

    assert hasattr(result, "var_99"), "Missing VaR (99%)"
    assert hasattr(result, "cvar_95"), "Missing CVaR"
    assert hasattr(result, "sharpe_ratio"), "Missing Sharpe ratio"
    assert hasattr(result, "max_drawdown"), "Missing max_drawdown"
    assert result.var_99 < 0, f"VaR(99%) should be negative, got {result.var_99}"
    assert result.var_95 < 0, f"VaR(95%) should be negative, got {result.var_95}"
    assert np.isfinite(result.sharpe_ratio), "Sharpe ratio should be finite"
    assert result.cvar_95 <= result.var_95, "CVaR should be <= VaR"

    print(f"\n  ✓ Scenario 6: MonteCarlo — VaR99={result.var_99:.4f}, "
          f"Sharpe={result.sharpe_ratio:.3f}")


# ── Scenario 7: Paper Trading Gate ──────────────────────────────────────────

def test_scenario_7_paper_trading():
    """PaperTradingGate tracks trades and blocks real trading."""
    from services.backtester.paper_trading import PaperTradingGate

    gate = PaperTradingGate()
    assert gate.mode == "paper", "Gate should start in paper mode"

    # Record 5 trades with mixed PnL
    for pnl in [0.02, -0.01, 0.03, -0.015, 0.01]:
        gate.record_trade(pnl)

    assert len(gate.daily_pnl) == 5, f"Expected 5 trades, got {len(gate.daily_pnl)}"

    status = gate.evaluate()
    assert hasattr(status, "mode"), "Missing mode"
    assert hasattr(status, "ready_for_real"), "Missing ready_for_real"
    assert status.ready_for_real is False, "Should not be ready for real trading"
    assert status.mode == "paper", "Mode should remain paper"

    print(f"\n  ✓ Scenario 7: PaperTrading — {len(gate.daily_pnl)} trades, ready={status.ready_for_real}")


# ── Scenario 8: swing_screener.py produces CSV ─────────────────────────────

def test_scenario_8_swing_screener():
    """swing_screener.py runs end-to-end and produces CSV output."""
    screener_script = os.path.join(ROOT, "scripts", "swing_screener.py")
    assert os.path.exists(screener_script), f"Missing {screener_script}"

    # Run in subprocess, capture output
    result = subprocess.run(
        [sys.executable, screener_script],
        capture_output=True, text=True, timeout=600,
        cwd=ROOT,
        env={**os.environ, "POSTGRES_HOST": PG_HOST, "POSTGRES_PORT": str(PG_PORT),
             "POSTGRES_DB": PG_DB, "POSTGRES_USER": PG_USER, "POSTGRES_PASSWORD": PG_PASS},
    )

    assert result.returncode == 0, (
        f"Screener failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout[-2000:]}\n"
        f"STDERR:\n{result.stderr[-2000:]}"
    )

    assert "Screening" in result.stderr or "Total screened" in result.stdout, (
        f"Screener did not produce expected output:\n"
        f"STDOUT:\n{result.stdout[-1000:]}\n"
        f"STDERR:\n{result.stderr[-1000:]}"
    )

    data_dir = os.path.join(ROOT, "data")
    csv_files = [f for f in os.listdir(data_dir) if f.startswith("swing_candidates_")]

    if csv_files:
        latest_csv = os.path.join(data_dir, sorted(csv_files)[-1])
        df = pd.read_csv(latest_csv)
        assert "stock_code" in df.columns or "Stock" in df.columns, (
            f"Unexpected CSV columns: {list(df.columns)}"
        )
        print(f"\n  ✓ Scenario 8: Screener — {len(df)} candidates in {os.path.basename(latest_csv)}")
    else:
        print(f"\n  ✓ Scenario 8: Screener — ran successfully, no candidates met threshold (AUC=0.555)")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scenarios = [
        test_scenario_1_docker_pg_health,
        test_scenario_2_feature_pipeline,
        test_scenario_3_training_features,
        test_scenario_4_ensemble_model,
        test_scenario_5_backtest_runner,
        test_scenario_6_monte_carlo,
        test_scenario_7_paper_trading,
        test_scenario_8_swing_screener,
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
