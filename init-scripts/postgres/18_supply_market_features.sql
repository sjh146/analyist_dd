-- 수급·시장·모멘텀 피처 테이블 (R10 — docs/DEAD_FEATURE_REVIVAL.md #4·#5)
--
-- WHY(2026-09-25 실측): feature_coverage 에서 수급 10 + 시장·모멘텀 9 = 19개 피처가
-- nonzero_ratio=0 이었다. 원천 테이블은 이미 보유 중이었다:
--   foreign_institutional 87,895행(2025-08-14~2026-09-23, 343종목×271거래일)
--   krx_short_selling      2,393행(2026-05-26~2026-09-23, 30종목×84거래일)
--   market_data        1,113,634행(2025-06-16~2026-09-23, 3,970종목×314거래일)
--   ownership               225행(2026-09-23, 225종목)
-- 부재한 것은 원천→피처 변환 코드였다. 이 테이블은 그 변환 결과를 종목×거래일 격자로
-- 저장한다(격자 기준 = market_data 의 (stock_code, trade_date)).
--
-- as-of 규율: 모든 값은 당일 종가까지의 정보로만 계산된다(미래 누수 없음).
--   · 수급 5일 누적 = 당일 포함 직전 5거래일 SUM(foreign_institutional 행 기준)
--   · 모멘텀 = 당일 종가/63·252거래일 전 종가 (이력 부족 시 NULL — 0으로 위장하지 않음)
--   · 공매도 = 당일 행(krx_short_selling), 잔고 기반 비율은 잔고 컬럼이 채워진 날부터
--   · 뉴스 충격도 = 당일 23:59:59.999999 as-of 창(최근 24h / 과거 7d 베이스라인)
--
-- 결측 규율: 값이 없으면 NULL(정보 없음)이며 0과 구분된다. 예외:
--   · retail_ownership_pct: institution_ownership_pct 가 원천에서 전부 NULL(실측
--     2026-09-25: ownership 225행 중 비결측 0행)이라 파이프라인 정의대로
--     COALESCE(기관,0) → 100 - 외국인 - 기관 으로 계산(개인 지분의 상한 근사).
--   · 시장레벨 피처(krx_advance_decline_ratio·market_breadth·krx_total_trading_value)는
--     모든 종목이 같은 값을 갖는다(횡단면 무변별 — 의도된 설계, feature_coverage 의
--     cross_section_constant_ratio=1 로 표시됨).
--
-- 계산식 상세: scripts/build_supply_market_features.py docstring.
CREATE TABLE IF NOT EXISTS supply_market_features (
    stock_code                 VARCHAR(10) NOT NULL,
    trade_date                 DATE        NOT NULL,
    institution_net_buy        DOUBLE PRECISION,   -- 당일 기관 순매수(원)
    institution_net_buy_5d     DOUBLE PRECISION,   -- 직전 5거래일 기관 순매수 누적(원)
    foreign_net_buy            DOUBLE PRECISION,   -- 당일 외국인 순매수(원)
    foreign_net_buy_5d         DOUBLE PRECISION,   -- 직전 5거래일 외국인 순매수 누적(원)
    foreign_ownership_pct      DOUBLE PRECISION,   -- 외국인 보유비율(%, ownership as-of)
    institution_ownership_pct  DOUBLE PRECISION,   -- 기관 보유비율(%, ownership as-of)
    retail_ownership_pct       DOUBLE PRECISION,   -- 개인 보유비율 추정(100-외국인-기관)
    short_interest_ratio       DOUBLE PRECISION,   -- 공매도잔고/총주식수(잔고 컬럼 보유분부터)
    short_selling_ratio        DOUBLE PRECISION,   -- 당일 공매도거래량/총거래량
    days_to_cover              DOUBLE PRECISION,   -- 공매도잔고/20거래일 평균거래량
    momentum_3_12m             DOUBLE PRECISION,   -- 12개월 수익률 - 3개월 수익률
    momentum_ni                DOUBLE PRECISION,   -- 순이익 직전보고서 대비 변화율(%)
    momentum_op                DOUBLE PRECISION,   -- 영업이익 직전보고서 대비 변화율(%)
    krx_advance_decline_ratio  DOUBLE PRECISION,   -- 당일 상승/하락 종목수(시장레벨)
    krx_total_trading_value    DOUBLE PRECISION,   -- 당일 KOSPI 총 거래대금(원, krx_trading)
    market_breadth             DOUBLE PRECISION,   -- 당일 상승종목 비율(시장레벨)
    market_impact_score        DOUBLE PRECISION,   -- 뉴스 서지 기반 시장충격도(0~100)
    relative_strength          DOUBLE PRECISION,   -- 종목 1일 수익률 - 시장 동일가중 수익률
    bb_position                DOUBLE PRECISION,   -- 볼린저 %B(20, 2σ)
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_supply_mkt_features_date ON supply_market_features (trade_date);
CREATE INDEX IF NOT EXISTS idx_supply_mkt_features_stock ON supply_market_features (stock_code);
