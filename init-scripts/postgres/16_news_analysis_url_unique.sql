-- 16_news_analysis_url_unique.sql — news_analysis(url) 유니크 인덱스
--
-- WHY: news-analyzer 는 `INSERT INTO news_analysis ... ON CONFLICT (url) DO NOTHING`
--   (app/storage/postgres_storage.py:74)로 저장하는데, 테이블에 url 유니크 제약이 없어
--   PostgreSQL 이 매 저장을 거부했다:
--     ERROR: there is no unique or exclusion constraint matching the ON CONFLICT specification
--
-- 실측 피해(2026-09-25):
--   - 마지막 저장 성공: 2026-09-23 16:08 / 24시간 저장 실패 3,657건
--   - 앱은 30분 주기로 계속 분석(DeepSeek 호출 결제)하면서 결과를 전부 폐기 → 뉴스 피처 데이터 공백
--
-- 검증 후 적용: 적용 전 중복 검사 = 전체 7,728행 / url 전부 존재 / 중복 그룹 0,
--   트랜잭션 안에서 생성→롤백으로 사전 검증(부작용 0) 후 실제 적용.
--   컨테이너 재시작 불필요(기사 단위 저장이라 인덱스 생성 즉시 다음 저장이 성공).

CREATE UNIQUE INDEX IF NOT EXISTS uq_news_analysis_url ON news_analysis(url);
