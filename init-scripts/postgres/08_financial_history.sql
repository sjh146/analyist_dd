-- ===========================================
-- DART 재무 이력 백필 지원 (M5 데이터 보강)
-- financial_statements 에 누락 컬럼 추가 (멱등)
-- ===========================================

ALTER TABLE financial_statements
    ADD COLUMN IF NOT EXISTS gross_profit DECIMAL(30,4);

ALTER TABLE financial_statements
    ADD COLUMN IF NOT EXISTS total_debt DECIMAL(30,4);

CREATE INDEX IF NOT EXISTS idx_financial_statements_stock_date
    ON financial_statements(stock_code, report_date);

-- End of 08_financial_history.sql
