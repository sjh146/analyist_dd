-- 데이터 품질 "정확성" 메트릭용 스키마 (2026-09-25)
--
-- WHY: 기존 감시(신선도/0거래량/동결/dead feature 수)는 "행이 없다" 유형만 잡는다.
-- 2026-09-24 실측에서 드러난 실패는 전부 **행도 있고 값도 있는데 틀린** 유형이었다:
--   ① NaN 을 값으로 세는 커버리지 착시 — pandas/numpy 에서 `col != 0` 이 NaN 을 True 로
--      평가해 SNS 커버리지가 0.99 로 보였으나 실제로는 대부분 결측이었다.
--   ② as-of 위반 — 최신 재무 스냅샷(2026-06-30)을 2025-08 행에 적용(최대 10개월 누수).
--      `get_financial_features` 가 date 를 받지 않아 종목당 값이 1개였다.
--   ③ 종목 상수 피처 15+개(net_income, roe, quality_roa, per_current 등)가 top30 선별을 지배.
--   ④ 날짜 내 분산 0(시장레벨) 피처 34개 — 횡단면 모델에서 무변별인데 피처로 소비됨.
--   ⑤ 상장 전 all-zero 패딩(스카이랩스 139행) — 저장 전 제거했으나 재발 감지 수단이 없었다.
--   ⑥ 러너 자기신고 ≠ 실제 적재량 — 확장 러너가 `+0행`으로 성공 종료(파서 키 불일치).
--
-- NOTE: init-scripts 는 **신규 볼륨에서만** 실행된다. 기존 DB 에는 수동 적용:
--   docker exec -i stock_postgres psql -U stock_user -d stock_trading < init-scripts/postgres/14_dq_metrics.sql

-- ── ① feature_coverage 확장: 커버리지 정직성 + 상수/시장레벨 판정 ──────────────
-- nonzero_ratio        = 기존값(하위호환). NaN 을 0 으로 채운 뒤 센 값 — 정직한 값은 아니었다.
-- nonzero_ratio_naive  = `col != 0` 그대로 (NaN 을 non-zero 로 세는 착시 재현)
-- nonzero_ratio_honest = `col IS NOT NULL AND col != 0` (정직한 커버리지)
-- null_ratio           = 결측 비율
-- stock_unique_median  = 종목별 (비결측 유니크값 개수) 의 중앙값 → 1 이하면 종목 상수
-- cross_section_constant_ratio = 날짜별 (종목간 분산 0) 인 날짜의 비율 → 1.0 이면 시장레벨
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS nonzero_ratio_naive DOUBLE PRECISION;
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS nonzero_ratio_honest DOUBLE PRECISION;
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS null_ratio DOUBLE PRECISION;
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS stock_unique_median DOUBLE PRECISION;
ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS cross_section_constant_ratio DOUBLE PRECISION;

-- ── ⑥ 러너 자기신고 원장 ────────────────────────────────────────────────────
-- 러너는 "이 테이블에 지금 N행 있다"고 주장(claim)을 남기고, 검증 쿼리가 실제
-- COUNT(*) 와 비교한다. 자기신고와 실적재량의 차이(gap)가 메트릭이 된다.
CREATE TABLE IF NOT EXISTS dq_runner_claim (
    id          BIGSERIAL PRIMARY KEY,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    runner      TEXT        NOT NULL,
    table_name  TEXT        NOT NULL,
    claimed_rows BIGINT     NOT NULL,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS dq_runner_claim_latest_idx
    ON dq_runner_claim (runner, table_name, run_at DESC);

COMMENT ON TABLE dq_runner_claim IS
    '수집 러너의 자기신고(적재 행수 주장). dq_runner_claim_gap 메트릭의 원천 — #6 실패유형 재발 감지.';
