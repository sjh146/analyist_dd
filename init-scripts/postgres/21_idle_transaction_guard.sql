-- 유휴 트랜잭션 가드 (2026-09-28 실측 사고): ML 파이프라인의 읽기 경로가 commit/rollback 없이
-- 커서만 닫아 연결 하나가 21시간 idle in transaction 으로 남았다(PgIdleInTransactionTooLong).
-- 코드도 고쳤지만(반납 전 rollback) 빠뜨린 경로가 또 생기지 않도록 DB 레벨에서도 정리한다.
-- idle = 트랜잭션만 열려 있고 질의가 없는 상태이므로, 장시간 배치(활성 질의)는 영향받지 않는다.
ALTER ROLE stock_user SET idle_in_transaction_session_timeout = '30min';
