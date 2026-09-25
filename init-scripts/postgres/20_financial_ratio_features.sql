-- 재무 비율 피처 테이블 (R12 — docs/QUANT_RESEARCH_BACKLOG.json R12, 재무 비율 19개 부활)
--
-- WHY(2026-09-25 실측): feature_coverage 에서 재무 비율 19개가 nonzero_ratio=0 이었다
--   (조회: `docker exec stock_postgres psql -U stock_user -d stock_trading -tAc "SELECT
--   feature_name, nonzero_ratio, computed_at FROM feature_coverage WHERE feature_name IN
--   ('value_per','value_pbr','value_psr','value_pcr','value_ncav','value_ev_ebit',
--   'value_pfcr','quality_cp_to_assets','quality_op_to_equity','quality_roe','quality_roa',
--   'quality_f_score','quality_asset_growth','quality_debt_ratio_change','quality_op_growth',
--   'roe','per_current','pbr_current')"` → 19개 전부 0, computed_at 2026-09-24).
--   원천은 이미 보유 중이었다: financial_statements 10,370행 = 2023/2024/2025-12-31 연간
--   2,592×3 + 2026-06-30 반기 2,588 (조회: SELECT report_date, COUNT(*) FROM
--   financial_statements GROUP BY 1) + disclosures 212,861행(2025-01-02~2026-09-23, 보고서명
--   '사업보고서 (2024.12)' 형태) + market_data 1,113,634행(종가). 부재한 것은
--   원천→피처 변환·적재 코드였다. 이 테이블은 그 결과를 **종목×거래일 격자**로 저장한다.
--
-- as-of 규율 (미래 누수 없음 — R12 method "접수일 이후에만 값이 보이도록 조인"):
--   · 격자 = market_data 의 (stock_code, trade_date). 거래일 D 행에는 **rcept_dt <= D**
--     인 재무제표 중 report_date 가 가장 큰(최신) 행의 비율만 붙인다(공시 당일 포함 —
--     17_event_features.sql 의 "공시는 장중 공개" 규율과 동일).
--   · rcept_dt 는 disclosures 의 보고서명(사업/반기/분기보고서 (YYYY.MM))에서 기간말을
--     파싱해 financial_statements.report_date 와 정합한 접수일이다. 매칭 실측
--     (period_end==report_date 조인): 2024-12-31 2,451/2,592, 2025-12-31 2,477/2,598,
--     2026-06-30 2,544/2,588. (stock, 기간) 당 접수건은 정확히 1건(19,484쌍 중 중복 0).
--   · rcept_dt 가 없고 report_date < 격자 시작(2025-06-16)이면 전 구간 가시로 처리한다
--     (접수일이 disclosures 백필 시작 2025-01-02 이전 — 실측: 2023-12-31 행 2,592건 전부,
--     2024-12-31 행 중 141건. 격자 시작보다 6개월 이상 이전 보고서라 룩어헤드 없음).
--     report_date >= 격자 시작인데 rcept_dt 가 없으면 **그 행은 제외**한다(접수 시점을
--     모르므로 가시 시점을 증명할 수 없음 — 실측 제외: 2025-12-31 121건, 2026-06-30 44건).
--     이 경우 해당 종목은 직전 가시 보고서 값이 유지된다.
--   · 시가총액 계열(PER/PBR/PSR/PCR/EV-EBIT/PFCR)은 거래일 D 의 종가 × 상장주식수로
--     계산한다(market_data 사용 — 스펙 지시). 상장주식수 = ownership.listed_shares
--     (2026-09-23 225종목) 우선, 없으면 stocks.market_cap ÷ 최신 종가로 역산
--     (실측 2026-09-25 조회: ownership 225종목 전부 역산값과 정확히 일치 — 225/225.
--     시총은 KRX 일별 공표값이라 listed_shares 와 같은 원천).
--   · 기간유형 구분: report_date 의 월로 period_type 을 나눈다(12=annual, 6=semi,
--     3/9=quarter). **성장률 계열(quality_asset_growth/debt_ratio_change/op_growth,
--     f_score 의 매출 비교, PFCR 의 재투자 프록시)은 같은 기간유형의 직전 행과만**
--     비교한다(연간↔반기 혼합 비교 금지). 반기 2026-06-30 행은 직전 반기(2025-06-30)가
--     원천에 없어 성장률 NULL(값을 만들지 않는다).
--   · 누적 손익의 연율화(TTM 근사): annual ×1, semi ×2, Q1 ×4, Q3 ×4/3 —
--     기존 refresh_valuation_ratios.py ANNUALIZE 규칙(2026-09-24 수립)과 동일 정의.
--     연율화 없이 섞으면 반기 보고서 수령일마다 ROA/ROE/PER 이 절반으로 튄다
--     (실측 삼성전자 2026-06-30 revenue 171.5조 = FY2025 333.6조의 약 절반).
--   · value_ncav: financial_statements 에 current_assets(유동자산) 컬럼이 없어
--     (실측 조회 2026-09-25: information_schema.columns = 0) NCAV 를 계산할 수 없다 —
--     **NULL 유지**(factor_features.py 의 기존 결정과 동일: 중복 피처 주입 금지).
--     컬럼이 추가되면 빌더 재실행으로 살아난다.
--   · 결측은 0 이 아니라 NULL 로 저장한다("정보 없음"을 0으로 위장하지 않는다).
--
-- 멱등성: 재실행 안전 — 이 빌더 전용 테이블이라 DELETE 후 COPY.
-- 계산식 상세·자기신고·검증: scripts/build_financial_ratio_features.py docstring.
CREATE TABLE IF NOT EXISTS financial_ratio_features (
    stock_code                  VARCHAR(10) NOT NULL,
    trade_date                  DATE        NOT NULL,
    period_type                 TEXT        NOT NULL,  -- 가시 재무제표 기간유형: annual/semi/quarter
    report_date                 DATE        NOT NULL,  -- 가시 재무제표 기간 말일
    rcept_dt                    DATE,                  -- 가시 재무제표 접수일(NULL=격자 시작 전 가시)
    value_per                   DOUBLE PRECISION,  -- 시총 / 연율화 순이익 (순이익>0 일 때만)
    value_pbr                   DOUBLE PRECISION,  -- 시총 / 자기자본 (자본>0)
    value_psr                   DOUBLE PRECISION,  -- 시총 / 연율화 매출 (매출>0)
    value_pcr                   DOUBLE PRECISION,  -- 시총 / 연율화 영업현금흐름 (>0)
    value_ncav                  DOUBLE PRECISION,  -- 시총 / NCAV — 유동자산 컬럼 부재로 NULL
    value_ev_ebit               DOUBLE PRECISION,  -- 시총 / 연율화 영업이익 (>0)
    value_pfcr                  DOUBLE PRECISION,  -- 시총 / 연율화 FCF (FCF>0)
    quality_cp_to_assets        DOUBLE PRECISION,  -- 연율화 OCF / 총자산 (자산>0)
    quality_op_to_equity        DOUBLE PRECISION,  -- 연율화 영업이익 / 자기자본 (자본>0)
    quality_roe                 DOUBLE PRECISION,  -- 연율화 순이익 / 자기자본 ×100
    quality_roa                 DOUBLE PRECISION,  -- 연율화 순이익 / 총자산 ×100
    quality_f_score             DOUBLE PRECISION,  -- 5점 척도(roe>0, 영업·순이익률>0, 부채<100, 매출증가)
    quality_asset_growth        DOUBLE PRECISION,  -- 동일 기간유형 직전 대비 자산 증가율(%)
    quality_debt_ratio_change   DOUBLE PRECISION,  -- 동일 기간유형 직전 대비 부채비율 차분(%p)
    quality_op_growth           DOUBLE PRECISION,  -- 동일 기간유형 직전 대비 연율화 영업이익 증가율(%)
    quality_earnings_volatility DOUBLE PRECISION,  -- 동일 기간유형 ni_ttm 이력(최대 8기) 표준편차
    roe                         DOUBLE PRECISION,  -- 기본 비율(quality_roe 와 동일 정의 — 파이프라인 이중 경로 반영)
    per_current                 DOUBLE PRECISION,  -- 기본 비율(value_per 와 동일 정의)
    pbr_current                 DOUBLE PRECISION,  -- 기본 비율(value_pbr 와 동일 정의)
    computed_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_financial_ratio_date ON financial_ratio_features (trade_date);
