-- ===========================================
-- PostgreSQL Feature Store
-- 주식 자동매매 시스템 - 피처 저장소
-- ===========================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- === 피처 저장소 스키마 ===
CREATE SCHEMA IF NOT EXISTS feature_store;

-- === 피처 정의 ===
CREATE TABLE IF NOT EXISTS feature_store.feature_definitions (
    feature_name VARCHAR PRIMARY KEY,
    category VARCHAR NOT NULL,
    description TEXT,
    version INT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === 피처 값 ===
-- INSERT ... ON CONFLICT (stock_code, feature_name, date) DO UPDATE SET value = EXCLUDED.value
CREATE TABLE IF NOT EXISTS feature_store.feature_values (
    stock_code VARCHAR NOT NULL,
    feature_name VARCHAR NOT NULL REFERENCES feature_store.feature_definitions(feature_name),
    date DATE NOT NULL,
    value DECIMAL(15,6),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, feature_name, date)
);

-- === 피처 배치 메타데이터 ===
CREATE TABLE IF NOT EXISTS feature_store.feature_metadata (
    batch_id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    stock_code VARCHAR NOT NULL,
    feature_count INT,
    computed_at TIMESTAMP,
    execution_time_ms INT
);

-- === 인덱스 ===
CREATE INDEX IF NOT EXISTS idx_feature_values_stock_date
    ON feature_store.feature_values(stock_code, date);
CREATE INDEX IF NOT EXISTS idx_feature_definitions_category
    ON feature_store.feature_definitions(category);
CREATE INDEX IF NOT EXISTS idx_feature_metadata_computed_at
    ON feature_store.feature_metadata(computed_at);
