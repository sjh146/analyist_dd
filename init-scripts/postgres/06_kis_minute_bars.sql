-- ===========================================
-- KIS 분봉 데이터 (2026-08, 비파괴 마이그레이션)
-- 단타 30분 채점(MinutePriceProvider) + 쌍둥이 실시간 분봉의 기반
-- ===========================================

CREATE TABLE IF NOT EXISTS minute_bars (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    trade_date DATE NOT NULL,
    "time" CHAR(6) NOT NULL,               -- HHMMSS (KIS stck_cntg_hour)
    open_price DECIMAL(20,4),
    high_price DECIMAL(20,4),
    low_price DECIMAL(20,4),
    close_price DECIMAL(20,4),
    volume BIGINT,
    trading_value DECIMAL(30,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (stock_code, trade_date, "time")
);

-- 일자별 조회 (수집/채점 공통)
CREATE INDEX IF NOT EXISTS idx_minute_bars_stock_date ON minute_bars(stock_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_minute_bars_date ON minute_bars(trade_date);
