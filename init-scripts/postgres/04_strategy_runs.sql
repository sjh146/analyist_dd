-- Quant strategy run history (Grafana Quant Strategy Monitoring 대시보드용)
-- 스윙스크리너/백테스트/강환국 팩터/모델 재학습 실행 결과 기록 (2026-08 신규)
CREATE TABLE IF NOT EXISTS strategy_runs (
    id BIGSERIAL PRIMARY KEY,
    tool TEXT NOT NULL,                    -- swing_screener / backtest / factor_<name> / model_retrain
    run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'ok',     -- ok / error
    stocks INTEGER,
    errors INTEGER,
    auc DOUBLE PRECISION,
    accuracy DOUBLE PRECISION,
    metric_value DOUBLE PRECISION,         -- factor: total_return / retrain: ensemble_auc
    meta JSONB
);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_tool ON strategy_runs (tool, run_at DESC);
