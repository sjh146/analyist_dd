-- 12_foreign_institutional_supply.sql
-- KIS OpenAPI 기반 종목별 투자자 수급(foreign_institutional) + 지분율(ownership) 스키마.
--
-- 배경
--  · KRX OpenAPI 의 투자자별 거래실적 서비스는 이 계정에 미승인(401 Unauthorized API Call)
--    → 종목별 수급 데이터의 유일한 경로는 KIS OpenAPI (FHKST01010900 /inquire-investor).
--  · 리더(xgboost-ml feature_engine)는 foreign_institutional 의
--    foreign_net_buy / institution_net_buy 를 읽고, 지분율 3피처는 **별도 `ownership` 테이블**에서
--    읽는다(market_features.py §get_supply_features, feature_pipeline.py §12~14).
--    `ownership` 테이블은 스키마에 존재하지 않았다 → 여기서 생성한다.
--
-- 단위 계약 (리더가 단위를 지정하지 않으므로 writer 가 고정한다)
--  · *_net_buy        = 순매수 **거래대금(대금, 원)**  ← KIS *_ntby_tr_pbmn(백만원) × 1e6
--  · *_net_buy_qty    = 순매수 **수량(주)**            ← KIS *_ntby_qty (원값)
--  · 부호 유지: 양수 = 순매수, 음수 = 순매도
--  · *_ownership_pct  = 지분율(%), retail = 100 - foreign - institution (리더 계산)

-- ── 1. foreign_institutional: 수량 컬럼 추가 (원값 보존·검증용) ──────────────
ALTER TABLE foreign_institutional
    ADD COLUMN IF NOT EXISTS foreign_net_buy_qty BIGINT,
    ADD COLUMN IF NOT EXISTS institution_net_buy_qty BIGINT,
    ADD COLUMN IF NOT EXISTS individual_net_buy_qty BIGINT;

COMMENT ON COLUMN foreign_institutional.foreign_net_buy IS
    '외국인 순매수 거래대금(원). KIS frgn_ntby_tr_pbmn(백만원)*1e6. 양수=순매수';
COMMENT ON COLUMN foreign_institutional.institution_net_buy IS
    '기관 순매수 거래대금(원). KIS orgn_ntby_tr_pbmn(백만원)*1e6. 양수=순매수';
COMMENT ON COLUMN foreign_institutional.individual_net_buy IS
    '개인 순매수 거래대금(원). KIS prsn_ntby_tr_pbmn(백만원)*1e6. 양수=순매수';
COMMENT ON COLUMN foreign_institutional.foreign_net_buy_qty IS
    '외국인 순매수 수량(주). KIS frgn_ntby_qty';

-- ── 2. ownership: 지분율 (리더가 읽는 테이블 — 스키마에 없었음) ──────────────
CREATE TABLE IF NOT EXISTS ownership (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    trade_date DATE NOT NULL,
    foreign_ownership_pct DECIMAL(8,2),
    institution_ownership_pct DECIMAL(8,2),
    -- 검증용 원값 (KIS /inquire-price): frgn_hldn_qty / lstn_stcn
    foreign_held_qty BIGINT,
    listed_shares BIGINT,
    source VARCHAR(20) DEFAULT 'kis',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (stock_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_ownership_stock_date ON ownership(stock_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_ownership_date ON ownership(trade_date);

COMMENT ON TABLE ownership IS
    '종목별 지분율. KIS /inquire-price 는 **현재 스냅샷만** 제공하므로(과거 조회 불가) '
    '행은 종목당 최신 거래일 1건이다. institution_ownership_pct 는 KIS 에 필드가 없어 NULL 이다.';
COMMENT ON COLUMN ownership.foreign_ownership_pct IS
    '외국인 지분율(%). KIS hts_frgn_ehrt (= frgn_hldn_qty/lstn_stcn*100 로 교차검증)';
COMMENT ON COLUMN ownership.institution_ownership_pct IS
    '기관 지분율(%). KIS OpenAPI 에 대응 필드 없음 → 항상 NULL (소스 부재, 소비 측 기본 0.0)';
