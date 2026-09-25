-- 13_krx_derivatives_program_migration.sql
-- 작성: 2026-09-24 (파생·프로그램매매 피처 복구 작업)
--
-- WHY
--   services/xgboost-ml/app/feature_engine/feature_pipeline.py 의 리더가 기대하는 컬럼이
--   krx_derivatives / krx_program_trading 테이블에 없어서 아래 피처가 **구조적으로 항상 0** 이었다.
--     - futures_premium      : SELECT close_price FROM krx_derivatives WHERE trade_date=%s AND index_name='KOSPI200'
--                              (764~780행) → close_price / index_name 컬럼 없음
--     - derivatives_volume   : SELECT SUM(volume) FROM krx_derivatives WHERE trade_date=%s (782~797행)
--     - program_trading_ratio: SELECT foreign_buy_value + foreign_sell_value, total_value
--                              FROM krx_program_trading WHERE trade_date=%s AND market='KOSPI' (515~533행)
--                              → foreign_buy_value / foreign_sell_value / total_value 컬럼 없음
--   추가로 basis / basis_change_5d 는 futures_options.basis 를 읽는다
--   (services/xgboost-ml/app/feature_engine/market_features.py get_derivatives_features, 194~225행).
--    futures_options 는 이미 존재하지만 데이터가 0행이었다.
--
-- 적재기(수집 스크립트)
--   - scripts/krx_derivatives_collect.py : KRX OpenAPI drv/fut_bydd_trd(+idx/kospi_dd_trd)
--       → krx_derivatives(상품별 1행/일) + futures_options(1행/일, basis 포함)
--   - scripts/kis_program_trading_collect.py : KIS FHPPG04600001(프로그램매매 종합조회(일별))
--       → krx_program_trading(시장별 1행/일)
--
-- 단위 규약
--   krx_program_trading.foreign_buy_value / foreign_sell_value / total_value / net_buy_value 는 **원(KRW)**.
--   KIS 프로그램 응답은 백만원 단위이므로 적재 시 ×1_000_000 한다(리더가 나눗셈만 하므로 단위는 일관되면 됨).

BEGIN;

-- 1) 파생상품 지수선물 (krx_derivatives)
ALTER TABLE krx_derivatives
    ADD COLUMN IF NOT EXISTS close_price NUMERIC(12, 2);

-- index_name: 리더가 index_name='KOSPI200' 으로 코스피200 선물 종가(=futures_premium)를 찾는다.
--            → 하루 1행만 이 값을 갖도록 적재기에서 프론트월 상품행에만 세팅한다.
ALTER TABLE krx_derivatives
    ADD COLUMN IF NOT EXISTS index_name VARCHAR(30);

COMMENT ON COLUMN krx_derivatives.close_price IS
    '종가 (price 와 동일 값 이중 기록: 리더가 close_price 를 조회)';
COMMENT ON COLUMN krx_derivatives.index_name IS
    '상품→지수 매핑 (KOSPI200 = 코스피200 선물 프론트월, KOSPI200_MINI, KOSDAQ150, KRX300, USD_KRW, KTB3Y, KTB10Y 등). 미매핑 상품은 NULL.';

-- 날짜+상품 단위 upsert 보장 (테이블이 비어 있어 충돌 없음)
CREATE UNIQUE INDEX IF NOT EXISTS uq_krx_derivatives_date_product
    ON krx_derivatives (trade_date, product_type);

-- 2) 프로그램매매 (krx_program_trading)
ALTER TABLE krx_program_trading
    ADD COLUMN IF NOT EXISTS foreign_buy_value BIGINT,
    ADD COLUMN IF NOT EXISTS foreign_sell_value BIGINT,
    ADD COLUMN IF NOT EXISTS total_value BIGINT,
    ADD COLUMN IF NOT EXISTS net_buy_value BIGINT;

COMMENT ON COLUMN krx_program_trading.foreign_buy_value IS
    '프로그램 매수 대금(원) — KIS 프로그램매매 종합조회(일별) whol_smtn_shnu_tr_pbmn ×1e6. '
    'KIS 일별 프로그램 API 는 투자자(외국인) 분리 값을 제공하지 않아 컬럼명과 달리 시장 전체 프로그램 매수를 담는다 '
    '(program_trading_ratio = 프로그램 매수+매도 / 시장 전체 거래대금 = 프로그램 매매 비중).';
COMMENT ON COLUMN krx_program_trading.foreign_sell_value IS
    '프로그램 매도 대금(원) — whol_smtn_seln_tr_pbmn ×1e6 (위와 동일한 주석 적용)';
COMMENT ON COLUMN krx_program_trading.total_value IS
    '해당 시장 전체 거래대금(원) — krx_trading(investor_type=''Total'') 기준, 없으면 market_data 합계';
COMMENT ON COLUMN krx_program_trading.net_buy_value IS
    '프로그램 순매수 대금(원) — whol_smtn_ntby_tr_pbmn ×1e6 (참고용)';

CREATE UNIQUE INDEX IF NOT EXISTS uq_krx_program_trading_date_market
    ON krx_program_trading (trade_date, market);

-- 3) futures_options — basis/basis_change_5d 리더(market_features)가 읽는 테이블.
--    01_schema.sql 에서 생성되지만, DB 가 스키마 초기화 없이 뜬 경우를 대비해 멱등 보장.
CREATE TABLE IF NOT EXISTS futures_options (
    id                      SERIAL PRIMARY KEY,
    trade_date              DATE NOT NULL,
    futures_price           NUMERIC(20, 4),
    options_volume          BIGINT,
    basis                   NUMERIC(20, 4),
    options_put_call_ratio  NUMERIC(10, 4),
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS futures_options_trade_date_key
    ON futures_options (trade_date);
COMMENT ON COLUMN futures_options.basis IS
    '코스피200 선물(프론트월) 종가 − 코스피200 지수 (지수 포인트 괴리). 출처: KRX drv/fut_bydd_trd '
    '(TDD_CLSPRC = 선물 종가, SPOT_PRC = 현물지수). 정의 근거는 scripts/krx_derivatives_collect.py 주석 참고.';

COMMIT;
