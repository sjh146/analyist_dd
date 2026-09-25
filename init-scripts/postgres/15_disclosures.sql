-- 15_disclosures.sql — DART 공시 인덱스 (rcept_dt 확보용)
--
-- WHY: financial_statements 에는 공시 접수일(rcept_dt)이 없다. 그래서
--   ① 재무 피처가 "사업보고서 90일 / 분기·반기 45일"이라는 **가정**으로만 as-of 정합을 맞춰 왔고
--      (services/xgboost-ml/app/feature_engine/company_features.py:30 주석),
--   ② financial_statements 가 연간(12-31)/반기(06-30) 혼재라 revenue_growth_yoy 가
--      H1-2026(71.6조) vs FY2025(45.2조)를 비교해 −48.6% 같은 엉뚱한 값을 낸다.
--   실제 접수일이 있으면 두 문제가 모두 풀린다.
--
-- 계약 출처: services/xgboost-ml/app/feature_engine/sentiment_features.py:79 가
--   disclosures(stock_code, rcept_dt DATE, report_nm) 를 이미 요구하고 있었으나 테이블이 없어
--   그 기능이 죽어 있었다.
--
-- DART list.json 응답 실측(2026-09-25): corp_code, corp_name, report_nm, rcept_dt, rcept_no,
--   stock_code 포함 → **corp_code 매핑 없이 종목 조인 가능**.

CREATE TABLE IF NOT EXISTS disclosures (
    stock_code VARCHAR(10) NOT NULL,
    rcept_dt   DATE        NOT NULL,
    rcept_no   VARCHAR(20) NOT NULL,
    report_nm  TEXT,
    corp_code  VARCHAR(10),
    PRIMARY KEY (stock_code, rcept_no)
);

CREATE INDEX IF NOT EXISTS idx_disclosures_stock_dt
    ON disclosures(stock_code, rcept_dt DESC);

-- report_nm 으로 정기공시 종류(사업/반기/분기)를 구분한다. 조회를 빠르게 하려고 표현식 인덱스 대신
-- 일반 인덱스를 둔다(행수가 작아 충분).
CREATE INDEX IF NOT EXISTS idx_disclosures_rcept_dt
    ON disclosures(rcept_dt DESC);
