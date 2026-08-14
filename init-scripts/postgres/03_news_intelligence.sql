-- ===========================================
-- News Intelligence Pipeline (Phase 2)
-- 구조화 이벤트 추출 (기사 → 정형 JSON 1개)
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

-- End of 03_news_intelligence.sql
