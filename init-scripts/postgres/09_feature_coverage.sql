-- 09_feature_coverage.sql — 피처 커버리지 (죽은 피처 조기 발견)
-- B-2: scripts/feature_coverage_report.py 가 훈련 패널의 각 피처에 대해
-- nonzero 비율/표준편차를 계산해 upsert한다. postgres-exporter 커스텀 쿼리가
-- 이 테이블을 읽어 feature_dead_count / feature_alive_count 메트릭으로 노출한다.
-- 멱등: CREATE TABLE IF NOT EXISTS

CREATE TABLE IF NOT EXISTS feature_coverage (
    feature_name TEXT PRIMARY KEY,
    nonzero_ratio DOUBLE PRECISION NOT NULL,
    std DOUBLE PRECISION NOT NULL,
    window_days INTEGER NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- End of 09_feature_coverage.sql
