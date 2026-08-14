-- ===========================================
-- News Intelligence Pipeline (Phase 2 + Phase 3)
-- Phase 2: 구조화 이벤트 추출 (기사 → 정형 JSON 1개)
-- Phase 3: 이벤트 클러스터링 (기사 → 이벤트 클러스터)
-- 멱등: CREATE TABLE IF NOT EXISTS
-- ===========================================

-- === 뉴스 이벤트 추출 ===
CREATE TABLE IF NOT EXISTS news_event_extraction (
    id SERIAL PRIMARY KEY,
    article_id INTEGER REFERENCES news_analysis(id),
    stock_code VARCHAR(10),
    event_type VARCHAR(50),
    themes JSONB,
    sentiment_score DECIMAL(5,4),
    importance DECIMAL(5,4),
    novelty DECIMAL(5,4),
    time_range VARCHAR(20),
    core_event_text TEXT,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_news_event_extraction_event_type CHECK (
        event_type IN (
            '실적발표',
            '배당',
            '유상증자·감자',
            'CB·BW',
            'M&A',
            '지분변동',
            '수주',
            '신제품',
            '특허',
            '규제',
            '소송',
            '부도·상폐·거래정지',
            '리콜',
            '자사주',
            '임원변경',
            '파트너십',
            '거시경제',
            '시장지수·유동성',
            '자연재해',
            '기타'
        )
    )
);

-- === 인덱스 ===
CREATE INDEX IF NOT EXISTS idx_news_event_extraction_article
    ON news_event_extraction(article_id);
CREATE INDEX IF NOT EXISTS idx_news_event_extraction_stock
    ON news_event_extraction(stock_code);
CREATE INDEX IF NOT EXISTS idx_news_event_extraction_event_type
    ON news_event_extraction(event_type);

-- === 이벤트 클러스터 (Phase 3) ===
-- embedding vector(384) 컬럼은 Phase 4에서 사용. Phase 3에서는 테이블에 포함만 하고
-- 임베딩 저장은 하지 않는다.
CREATE TABLE IF NOT EXISTS news_events (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10),
    event_type VARCHAR(50),
    event_date DATE,
    time_bucket VARCHAR(20),
    cluster_key VARCHAR(200),
    article_count INTEGER,
    first_article_at TIMESTAMP,
    last_article_at TIMESTAMP,
    total_importance DECIMAL(10,4),
    max_sentiment_abs DECIMAL(5,4),
    representative_core_event_text TEXT,
    embedding vector(384),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cluster_key)
);

CREATE INDEX IF NOT EXISTS idx_news_events_stock
    ON news_events(stock_code);
CREATE INDEX IF NOT EXISTS idx_news_events_event_type
    ON news_events(event_type);
CREATE INDEX IF NOT EXISTS idx_news_events_event_date
    ON news_events(event_date);

-- === 이벤트 임베딩 HNSW 인덱스 (Phase 4) ===
-- 384d vector_cosine_ops HNSW 인덱스. 유사 이벤트 검색(ORDER BY embedding <=> $q)에 사용.
-- 멱등: CREATE INDEX IF NOT EXISTS.
CREATE INDEX IF NOT EXISTS idx_news_events_embedding_hnsw
    ON news_events USING hnsw (embedding vector_cosine_ops);

-- End of 03_news_intelligence.sql
