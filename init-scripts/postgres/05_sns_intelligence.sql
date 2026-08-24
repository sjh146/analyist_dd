-- ===========================================
-- SNS Intelligence Pipeline (Phase A)
-- Phase A: SNS 게시글 수집 + 종목별 SNS 특징 집계
-- 멱등: CREATE TABLE IF NOT EXISTS
-- ===========================================

-- === SNS 게시글 원본 ===
-- 각 SNS 소스(네이버 종목토론방, X, 증권플러스, 네이버 카페)에서 수집한
-- 게시글 1건을 저장한다.
--
-- 중복 제거(dedup): (source, post_id) UNIQUE 제약으로 동일 소스의 동일
-- 게시글이 두 번 저장되지 않도록 보장한다. 수집기가 같은 게시글을 다시
-- 가져오면 INSERT 시 UNIQUE 위반이 발생하므로, 호출부는
-- `ON CONFLICT (source, post_id) DO NOTHING` 을 사용해 기존 행을 유지한다.
CREATE TABLE IF NOT EXISTS sns_posts (
    id SERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL,          -- 수집 소스 (naver_board, x, ...)
    post_id VARCHAR(200) NOT NULL,        -- 소스 내 고유 게시글 ID
    stock_code VARCHAR(10),               -- 관련 종목 코드 (없으면 NULL)
    author_id VARCHAR(200),               -- 작성자 고유 ID
    author_name VARCHAR(200),             -- 작성자 표시 이름
    author_followers INTEGER DEFAULT 0,   -- 작성자 팔로워 수
    posted_at TIMESTAMP,                  -- 게시 시각
    text TEXT,                            -- 게시글 본문/제목
    comment_count INTEGER DEFAULT 0,      -- 댓글 수
    like_count INTEGER DEFAULT 0,         -- 좋아요 수
    retweet_count INTEGER DEFAULT 0,      -- 리트윗/공유 수
    raw_json JSONB,                       -- 원본 JSON (디버깅/재처리용)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- 중복 제거: 동일 소스의 동일 게시글은 1건만 허용
    UNIQUE(source, post_id)
);

-- === 종목별 SNS 특징 집계 ===
-- 특정 종목의 특정 거래일에 대한 SNS 감정/관심/모멘텀/작성자 품질 점수를
-- 집계한다. Kalman 필터로 평활화된 시계열 값도 함께 저장한다.
CREATE TABLE IF NOT EXISTS sns_post_features (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL,      -- 종목 코드
    trade_date DATE NOT NULL,             -- 거래일
    sentiment_score DECIMAL(6,4),         -- 감정 점수 (-1.0 ~ 1.0)
    attention_score DECIMAL(6,4),         -- 관심도 점수 (0.0 ~ 1.0)
    momentum_score DECIMAL(6,4),          -- 모멘텀 점수
    author_quality_score DECIMAL(6,4),    -- 작성자 품질 점수
    post_count INTEGER DEFAULT 0,         -- 수집된 게시글 수
    bot_filtered_count INTEGER DEFAULT 0, -- 봇으로 필터링된 게시글 수
    -- Kalman 필터 평활화 값 (시계열 노이즈 제거)
    kalman_sentiment DECIMAL(6,4),        -- Kalman 평활 감정
    kalman_attention DECIMAL(6,4),        -- Kalman 평활 관심도
    kalman_momentum DECIMAL(6,4),         -- Kalman 평활 모멘텀
    kalman_activity DECIMAL(6,4),         -- Kalman 평활 활동량
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- 종목+거래일당 1행만 허용 (upsert 대상)
    UNIQUE(stock_code, trade_date)
);

-- === 인덱스 ===
CREATE INDEX IF NOT EXISTS idx_sns_posts_source
    ON sns_posts(source);
CREATE INDEX IF NOT EXISTS idx_sns_posts_stock
    ON sns_posts(stock_code);
CREATE INDEX IF NOT EXISTS idx_sns_posts_posted_at
    ON sns_posts(posted_at);
CREATE INDEX IF NOT EXISTS idx_sns_post_features_stock
    ON sns_post_features(stock_code);
CREATE INDEX IF NOT EXISTS idx_sns_post_features_trade_date
    ON sns_post_features(trade_date);

-- End of 05_sns_intelligence.sql
