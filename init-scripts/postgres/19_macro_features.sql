-- 거시 피처 테이블 (R11 — docs/QUANT_RESEARCH_BACKLOG.json R11, 거시 국면 16개)
--
-- WHY(2026-09-25 실측): feature_coverage 에서 fx/oil/interest/cpi/ppi/yield/economic/cycle
-- 계열 16개가 nonzero_ratio=0 이었다(조회: `docker exec stock_postgres psql -U stock_user
-- -d stock_trading -tAc "SELECT feature_name, nonzero_ratio FROM feature_coverage WHERE
-- feature_name IN ('fx_usd_krw','oil_wti','interest_rate','cpi_yoy','yield_spread',
-- 'cycle_up','economic_event_count_7d')"` → 전부 0, computed_at 2026-09-24).
-- 원천 macro_indicators 는 이미 보유 중이었다(6,261행 = USD/JPY/CNY 환율 751×3 + WTI 유가 749
-- + 기준금리 1,361(일별 캘린더) + 국고채3년/회사채3년 913×2 + CPI/PPI 36×2, 결측 0행).
-- 부재한 것은 원천 → 피처 변환·적재였다. 이 테이블은 그 결과를 **거래일 단위**로 저장한다
-- (거시 값은 종목 횡단면에서 동일하므로 종목×일자 격자 대신 일자 1열 PK — feature_coverage 의
-- cross_section_constant_ratio=1 로 표시된다).
--
-- as-of 규율 (미래 누수 없음):
--   · 일별 지표(환율·금리·유가·국고채): trade_date D 기준 **관측일 <= D 의 최신 값**(당일 사용).
--   · 월별 지표(CPI/PPI)는 **발표 지연**을 반영한다. macro_indicators.date 는 관측월
--     (매월 1일, 2026-08-01 이 최신)이므로 관측일 + 고정 지연을 발표일 근사로 쓴다:
--       CPI: 관측일 + 35일  (한국 CPI 는 익월 첫째 주 발표 → 익월 5~6일 이후에만 보임)
--       PPI: 관측일 + 55일  (한국 PPI 는 익월 셋째 주 발표 → 익월 24~25일 이후에만 보임)
--     발표일 자체는 DB 에 없어(economic_events 는 예정 캘린더일 뿐) 보수적 고정 지연을
--     채택했다. 관측월+지연 이전의 행에는 값이 존재하지 않는다(NOT 저장 — 룩어헤드 차단).
--   · 경제 이벤트: economic_events 는 **사전 공지된 발표 캘린더**(2026-01-28~2027-12-25,
--     291행)다. 창 (D-6, D] 의 이벤트만 센다(미래 이벤트는 event_date > D 로 제외).
--     캘린더 시작 전 구간은 창이 부분 커버 → NULL(정보 없음).
--   · 변화율 창은 파이프라인 reader(macro_features.py)와 동일 정의: 1m=30일, 3m=90일,
--     YoY=365일. 이력 부족 시 NULL(0으로 위장하지 않음).
--
-- 원천 실측 한계 (0 이 아니라 NULL 로 정직하게 남긴다):
--   · economic_events 는 actual_value/forecast_value 가 291행 전부 NULL(조회 2026-09-25:
--     COUNT(*) FILTER (WHERE actual_value IS NOT NULL) = 0) ⇒ economic_event_impact 는
--     서프라이즈가 아니라 **중요도 가중 강도**(high=2, medium=1)로 정의한다.
--
-- 계산식 상세: scripts/build_macro_features.py docstring.
CREATE TABLE IF NOT EXISTS macro_features (
    trade_date                  DATE        NOT NULL,
    fx_usd_krw                  DOUBLE PRECISION,   -- USD/KRW 환율 (원/달러, 당일 as-of)
    fx_change_1m                DOUBLE PRECISION,   -- 30일 대비 변화율(%)
    fx_change_3m                DOUBLE PRECISION,   -- 90일 대비 변화율(%)
    oil_wti                     DOUBLE PRECISION,   -- WTI 유가 (달러/배럴, 당일 as-of)
    oil_change_1m               DOUBLE PRECISION,   -- 30일 대비 변화율(%)
    oil_change_3m               DOUBLE PRECISION,   -- 90일 대비 변화율(%)
    interest_rate               DOUBLE PRECISION,   -- 기준금리 (%, 당일 as-of)
    interest_rate_change_1m     DOUBLE PRECISION,   -- 30일 대비 차분(%p)
    interest_rate_change_3m     DOUBLE PRECISION,   -- 90일 대비 차분(%p)
    cpi_yoy                     DOUBLE PRECISION,   -- CPI 전년동월비 (%p, 발표 지연 35일 반영)
    ppi_yoy                     DOUBLE PRECISION,   -- PPI 전년동월비 (%p, 발표 지연 55일 반영)
    yield_spread                DOUBLE PRECISION,   -- 국고채3년 - 기준금리 (%p)
    economic_event_count_7d     INTEGER,            -- 직전 7일(당일 포함) 경제 이벤트 건수
    economic_event_impact       DOUBLE PRECISION,   -- 같은 창의 중요도 가중 강도(high=2, medium=1)
    cycle_up                    SMALLINT    NOT NULL DEFAULT 0,  -- 기준금리 90일 차분 > 0 (인상 국면)
    cycle_down                  SMALLINT    NOT NULL DEFAULT 0,  -- 기준금리 90일 차분 < 0 (인하 국면)
    computed_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date)
);

CREATE INDEX IF NOT EXISTS idx_macro_features_date ON macro_features (trade_date);
