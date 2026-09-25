-- ============================================================================
-- krx_short_selling 보강 마이그레이션
-- ============================================================================
-- 배경
--   feature_pipeline.py(#19 short_selling_ratio)가
--       SELECT short_volume, total_volume FROM krx_short_selling ...
--   를 읽는데, 02_swing_pipeline.sql 의 CREATE TABLE 에 total_volume 컬럼이
--   없었다 → 컬럼 부재로 예외 → 피처가 항상 0.0 이었다.
--   또한 업서트(ON CONFLICT)에 필요한 (trade_date, stock_code) 유니크 제약이
--   없었다(비유니크 인덱스 idx_krx_short_date 만 존재).
--
-- 안전성
--   적용 시점에 테이블이 0행이었으므로 유니크 인덱스 추가에 충돌 위험이 없다.
--   모든 문장이 IF NOT EXISTS 라 반복 실행에 안전하다.
--
-- 적용 방법
--   기존 DB: docker exec -i stock_postgres psql -U stock_user -d stock_trading \
--              -v ON_ERROR_STOP=1 < init-scripts/postgres/11_krx_short_selling_migration.sql
--   신규 DB: docker-entrypoint-initdb.d 가 파일명 알파벳 순으로 실행하므로
--            02_swing_pipeline.sql 다음에 자동 적용된다.
-- ============================================================================

-- 1) 리더(feature_pipeline #19)가 요구하는 일별 총 거래량
ALTER TABLE krx_short_selling
    ADD COLUMN IF NOT EXISTS total_volume BIGINT DEFAULT 0;

-- 2) 종목명 (services/krx-collector 의 저장소 스키마와 일치시킨다)
ALTER TABLE krx_short_selling
    ADD COLUMN IF NOT EXISTS stock_name VARCHAR(100);

-- 3) 업서트 키: (trade_date, stock_code) 유니크
--    ON CONFLICT (trade_date, stock_code) DO UPDATE 가 이 인덱스를 요구한다.
CREATE UNIQUE INDEX IF NOT EXISTS uq_krx_short_selling_date_code
    ON krx_short_selling (trade_date, stock_code);

-- 4) 종목별 시계열 조회용 (alt_features.short_squeeze / 스크리너가 이 순서로 조회)
CREATE INDEX IF NOT EXISTS idx_krx_short_code_date
    ON krx_short_selling (stock_code, trade_date DESC);

-- 참고: balance_quantity(공매도 잔고)는 이 컬럼을 채울 수 있는 공개 소스가
-- 없어 NULL 로 남는다. 근거는 reports/... (아래 러너 docstring / 보고서) 참조.
