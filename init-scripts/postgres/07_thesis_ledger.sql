-- ===========================================
-- Thesis Ledger (테제원장) — M1
-- 빌 애크먼식: 매수 시점 테제 냉동 + 매일 판정 + '파기' 시 매도
-- thesis_verdicts 는 append-only 판정 원장 (INSERT 전용, UPDATE/DELETE DB 차단)
-- 멱등: CREATE TABLE/INDEX IF NOT EXISTS (02~06 파일 관례)
-- ===========================================

-- === 포지션별 투자 테제 (원장 마스터) ===
CREATE TABLE IF NOT EXISTS position_theses (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    strategy_name VARCHAR(50) NOT NULL DEFAULT 'ackman_fundamental',
    thesis_text TEXT NOT NULL,            -- "왜 사는가" 2~3문장 (AI 초안 + 사용자 승인)
    disproof_criteria TEXT NOT NULL,      -- 반박증거: "이게 보이면 즉시 매도"
    intrinsic_value DECIMAL(20,4),        -- 본질가치 추정 (MoS = intrinsic/entry - 1)
    entry_price DECIMAL(20,4),            -- 진입가
    catalyst_events JSONB,                -- 기대 촉매 [{event_type, desc, deadline}]
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- active / exited / thesis_broken
    decision_log JSONB,                   -- 분기 리뷰/테제 변경 이력 (append-only)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_position_theses_status CHECK (
        status IN ('active', 'exited', 'thesis_broken')
    )
);

-- === 매일 판정 원장 (append-only, 수정 금지) ===
CREATE TABLE IF NOT EXISTS thesis_verdicts (
    id SERIAL PRIMARY KEY,
    thesis_id INT NOT NULL REFERENCES position_theses(id),
    verdict_date DATE NOT NULL,
    verdict VARCHAR(20) NOT NULL,         -- 강화 / 유지 / 약화 / 손상 / 파기
    verdict_score DECIMAL(5,4),           -- -1(파기) ~ +1(강화)
    evidence_event_ids INT[],             -- 근거 news_event_extraction.id 목록
    evidence_summary TEXT,                -- DeepSeek 판정 근거 1~2문장
    model_version VARCHAR(50),            -- 판정 프롬프트 버전
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(thesis_id, verdict_date),
    CONSTRAINT chk_thesis_verdicts_verdict CHECK (
        verdict IN ('강화', '유지', '약화', '손상', '파기')
    ),
    CONSTRAINT chk_thesis_verdicts_score CHECK (
        verdict_score >= -1.0 AND verdict_score <= 1.0
    )
);

-- === 인덱스 ===
CREATE INDEX IF NOT EXISTS idx_thesis_verdicts_thesis
    ON thesis_verdicts(thesis_id, verdict_date DESC);
CREATE INDEX IF NOT EXISTS idx_position_theses_status
    ON position_theses(status);
CREATE INDEX IF NOT EXISTS idx_position_theses_stock
    ON position_theses(stock_code);

-- === append-only 강제: 판정 원장 UPDATE/DELETE 차단 (운영 안전장치) ===
CREATE OR REPLACE FUNCTION forbid_thesis_verdicts_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'thesis_verdicts is append-only: UPDATE/DELETE forbidden (thesis_id=%, verdict_date=%)',
        OLD.thesis_id, OLD.verdict_date;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_thesis_verdicts_no_update ON thesis_verdicts;
CREATE TRIGGER trg_thesis_verdicts_no_update
    BEFORE UPDATE OR DELETE ON thesis_verdicts
    FOR EACH ROW EXECUTE FUNCTION forbid_thesis_verdicts_mutation();

-- End of 07_thesis_ledger.sql
