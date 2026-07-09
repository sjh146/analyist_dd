-- ===========================================
-- PostgreSQL + pgvector Schema
-- 주식 자동매매 시스템
-- ===========================================

CREATE EXTENSION IF NOT EXISTS vector;

-- === 종목 마스터 ===
CREATE TABLE stocks (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) UNIQUE NOT NULL,
    stock_name VARCHAR(100) NOT NULL,
    market VARCHAR(10) NOT NULL,
    sector VARCHAR(100),
    industry VARCHAR(100),
    market_cap BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === 시장 데이터 ===
CREATE TABLE market_data (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    trade_date DATE NOT NULL,
    open_price DECIMAL(20,4),
    high_price DECIMAL(20,4),
    low_price DECIMAL(20,4),
    close_price DECIMAL(20,4),
    volume BIGINT,
    trading_value DECIMAL(30,4),
    UNIQUE(stock_code, trade_date)
);

-- === 종목 벡터 (pgvector) ===
CREATE TABLE stock_vectors (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    vector_type VARCHAR(50) NOT NULL,
    embedding vector(1024),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, vector_type)
);

CREATE INDEX idx_stock_vectors_hnsw ON stock_vectors
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

-- === 뉴스/SNS 분석 ===
CREATE TABLE news_analysis (
    id SERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    url VARCHAR(500),
    published_at TIMESTAMP,
    authenticity_score DECIMAL(5,4),
    authenticity_label VARCHAR(20),
    sentiment_score DECIMAL(5,4),
    sentiment_label VARCHAR(20),
    confidence DECIMAL(5,4),
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_response JSONB
);

-- === 종목별 감정 집계 ===
CREATE TABLE stock_sentiment (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    analysis_date DATE NOT NULL,
    avg_sentiment DECIMAL(5,4),
    sentiment_count INTEGER DEFAULT 0,
    positive_count INTEGER DEFAULT 0,
    negative_count INTEGER DEFAULT 0,
    neutral_count INTEGER DEFAULT 0,
    avg_authenticity DECIMAL(5,4),
    news_count INTEGER DEFAULT 0,
    sns_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, analysis_date)
);

-- === ML 예측 ===
CREATE TABLE ml_predictions (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    prediction_date DATE NOT NULL,
    model_version VARCHAR(50),
    predicted_direction VARCHAR(10),
    predicted_change_pct DECIMAL(10,4),
    confidence DECIMAL(5,4),
    features_used JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, prediction_date, model_version)
);

-- === 매매 주문 ===
CREATE TABLE trade_orders (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    order_type VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    price DECIMAL(20,4),
    order_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    strategy_name VARCHAR(50),
    signal_source VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    executed_at TIMESTAMP,
    creon_order_id VARCHAR(50)
);

-- === 포지션 ===
CREATE TABLE positions (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    quantity INTEGER NOT NULL DEFAULT 0,
    avg_buy_price DECIMAL(20,4),
    current_price DECIMAL(20,4),
    unrealized_pnl DECIMAL(20,4) DEFAULT 0,
    realized_pnl DECIMAL(20,4) DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code)
);

-- === 전략 설정 ===
CREATE TABLE strategy_config (
    id SERIAL PRIMARY KEY,
    strategy_name VARCHAR(50) UNIQUE NOT NULL,
    strategy_type VARCHAR(50) NOT NULL,
    parameters JSONB NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === 리스크 관리 ===
CREATE TABLE risk_management (
    id SERIAL PRIMARY KEY,
    rule_name VARCHAR(100) UNIQUE NOT NULL,
    rule_type VARCHAR(50) NOT NULL,
    parameters JSONB NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === 인덱스 ===
CREATE UNIQUE INDEX idx_market_data_stock_date ON market_data(stock_code, trade_date);
CREATE UNIQUE INDEX idx_stock_sentiment_stock_date ON stock_sentiment(stock_code, analysis_date);
CREATE UNIQUE INDEX idx_ml_predictions_stock_date ON ml_predictions(stock_code, prediction_date, model_version);
CREATE INDEX idx_trade_orders_status ON trade_orders(order_status);
CREATE INDEX idx_trade_orders_created ON trade_orders(created_at);

-- ===========================================
-- Feature Engineering Pipeline 확장 (2026-07)
-- ===========================================

-- === 재무제표 데이터 ===
CREATE TABLE financial_statements (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    report_date DATE NOT NULL,
    revenue DECIMAL(30,4),
    operating_profit DECIMAL(30,4),
    net_income DECIMAL(30,4),
    total_assets DECIMAL(30,4),
    total_equity DECIMAL(30,4),
    per DECIMAL(10,4),
    pbr DECIMAL(10,4),
    roe DECIMAL(10,4),
    debt_ratio DECIMAL(10,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, report_date)
);

-- === 거시경제 지표 ===
CREATE TABLE macro_indicators (
    id SERIAL PRIMARY KEY,
    indicator_name VARCHAR(100) NOT NULL,
    date DATE NOT NULL,
    value DECIMAL(20,4),
    unit VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(indicator_name, date)
);

-- === 외국인/기관 수급 ===
CREATE TABLE foreign_institutional (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    trade_date DATE NOT NULL,
    foreign_net_buy DECIMAL(30,4),
    institution_net_buy DECIMAL(30,4),
    individual_net_buy DECIMAL(30,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, trade_date)
);

-- === 선물·옵션 ===
CREATE TABLE futures_options (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL UNIQUE,
    futures_price DECIMAL(20,4),
    options_volume BIGINT,
    basis DECIMAL(20,4),
    options_put_call_ratio DECIMAL(10,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === 신규 테이블 인덱스 ===
CREATE INDEX idx_financial_statements_stock ON financial_statements(stock_code);
CREATE INDEX idx_financial_statements_date ON financial_statements(report_date);
CREATE INDEX idx_macro_indicators_name ON macro_indicators(indicator_name);
CREATE INDEX idx_macro_indicators_date ON macro_indicators(date);
CREATE INDEX idx_foreign_inst_stock ON foreign_institutional(stock_code);
CREATE INDEX idx_foreign_inst_date ON foreign_institutional(trade_date);
CREATE INDEX idx_futures_options_date ON futures_options(trade_date);

-- === 뉴스 임베딩 (pgvector) ===
ALTER TABLE news_analysis ADD COLUMN IF NOT EXISTS embedding vector(1024);

CREATE INDEX IF NOT EXISTS idx_news_embedding_hnsw ON news_analysis
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);
