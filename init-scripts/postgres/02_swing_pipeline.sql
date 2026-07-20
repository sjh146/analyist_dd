-- 02_swing_pipeline.sql — Tables for KRX data, economic calendar, US market data

CREATE TABLE IF NOT EXISTS krx_trading (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    market VARCHAR(10) NOT NULL,
    investor_type VARCHAR(20) NOT NULL,
    trading_value BIGINT NOT NULL,
    net_buy BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_krx_trading_date ON krx_trading(trade_date);

CREATE TABLE IF NOT EXISTS krx_derivatives (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    product_type VARCHAR(30) NOT NULL,
    contract_type VARCHAR(10),
    price DECIMAL(12,2),
    volume BIGINT,
    open_interest BIGINT,
    change_pct DECIMAL(6,2),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_krx_derivatives_date ON krx_derivatives(trade_date);

CREATE TABLE IF NOT EXISTS krx_short_selling (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    stock_code VARCHAR(12) NOT NULL,
    short_volume BIGINT,
    short_value BIGINT,
    short_ratio DECIMAL(6,2),
    balance_quantity BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_krx_short_date ON krx_short_selling(trade_date, stock_code);

CREATE TABLE IF NOT EXISTS krx_program_trading (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    market VARCHAR(10) NOT NULL,
    foreign_ownership_pct DECIMAL(8,2),
    limit_exhaustion_rate DECIMAL(8,2),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS economic_events (
    id SERIAL PRIMARY KEY,
    event_date DATE NOT NULL,
    event_time TIME,
    country VARCHAR(10) NOT NULL,
    category VARCHAR(30) NOT NULL,
    title VARCHAR(200) NOT NULL,
    importance VARCHAR(10) DEFAULT 'medium',
    previous_value VARCHAR(50),
    forecast_value VARCHAR(50),
    actual_value VARCHAR(50),
    is_updated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_economic_events_date ON economic_events(event_date);
CREATE INDEX IF NOT EXISTS idx_economic_events_cat ON economic_events(category);

CREATE TABLE IF NOT EXISTS us_market_data (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    index_name VARCHAR(30) NOT NULL,
    open_price DECIMAL(12,4),
    high_price DECIMAL(12,4),
    low_price DECIMAL(12,4),
    close_price DECIMAL(12,4),
    volume BIGINT,
    change_pct DECIMAL(6,2),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_us_market_date ON us_market_data(trade_date, index_name);

CREATE TABLE IF NOT EXISTS foreign_investor_futures (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    product_type VARCHAR(30) NOT NULL,
    net_buy_contracts INTEGER,
    cumulative_position INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

-- End of 02_swing_pipeline.sql
