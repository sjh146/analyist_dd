"""save_prediction 회귀 테스트 — 죽은 DB 연결이 하루치 예측 저장을 중단시키지 않아야 한다.

왜(2026-09-28 실측 사고): Postgres 가 연결을 끊자 cur.execute 가 OperationalError('server closed
the connection unexpectedly') 를 던졌고, except 블록의 conn.rollback() 이 다시 InterfaceError
('connection already closed') 를 던져 **그 예외가 저장 루프 밖으로 새어나갔다** → 그날
ml_predictions 가 0행이 됐다(아침 피처/시그널에서 '예측 없음'). 죽은 연결은 풀에 그대로
반납돼 다음 저장까지 연쇄 실패했다.

여기서 고정하는 성질:
  1) 연결 계열 오류는 연결을 버리고 1회 재시도한다(성공하면 True).
  2) 재시도까지 실패해도 예외를 던지지 않고 False 로 보고한다.
  3) 죽은 연결은 close=True 로 풀에 반납된다(재사용 금지).
  4) rollback 이 죽은 연결에서 예외를 던져도 원래 흐름을 깨지 않는다.
"""

from __future__ import annotations

import unittest
from unittest import mock

import psycopg2

from app.storage.postgres_storage import PostgresStorage


class _FakeCursor:
    def __init__(self, exc=None):
        self._exc = exc
        self.closed = False

    def execute(self, *args, **kwargs):
        if self._exc:
            raise self._exc

    def close(self):
        self.closed = True


class _FakeConn:
    def __init__(self, execute_exc=None, rollback_exc=None):
        self.closed = 0
        self._execute_exc = execute_exc
        self._rollback_exc = rollback_exc
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self._execute_exc)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        if self._rollback_exc:
            raise self._rollback_exc


PRED = {
    "stock_code": "005930",
    "prediction_date": "2026-09-28",
    "model_version": "v1.0",
    "predicted_direction": "up",
    "predicted_probability": 0.61,
    "confidence": 0.22,
    "features_used": ["rsi"],
}


def _storage() -> PostgresStorage:
    with mock.patch.object(PostgresStorage, "_init_pool", lambda self: None):
        st = PostgresStorage()
    st._pool = mock.Mock()
    return st


class SavePredictionResilienceTest(unittest.TestCase):
    def test_success_path(self) -> None:
        st = _storage()
        conn = _FakeConn()
        st._pool.getconn.return_value = conn
        self.assertTrue(st.save_prediction(PRED))
        self.assertEqual(conn.commits, 1)
        st._pool.putconn.assert_called_once_with(conn, close=False)

    def test_retries_once_on_broken_connection(self) -> None:
        """첫 시도 OperationalError → 새 연결로 재시도 → 성공(True)."""
        st = _storage()
        dead = _FakeConn(execute_exc=psycopg2.OperationalError("server closed the connection unexpectedly"))
        good = _FakeConn()
        st._pool.getconn.side_effect = [dead, good]
        self.assertTrue(st.save_prediction(PRED))
        self.assertEqual(good.commits, 1)
        # 죽은 연결은 닫아서 반납, 살아있는 연결은 정상 반납
        self.assertIn(mock.call(dead, close=True), st._pool.putconn.call_args_list)
        self.assertIn(mock.call(good, close=False), st._pool.putconn.call_args_list)

    def test_gives_up_with_false_not_exception(self) -> None:
        """재시도까지 실패해도 예외를 던지지 않는다(루프가 살아 있어야 한다)."""
        st = _storage()
        dead1 = _FakeConn(execute_exc=psycopg2.InterfaceError("connection already closed"))
        dead2 = _FakeConn(execute_exc=psycopg2.OperationalError("server closed the connection unexpectedly"))
        st._pool.getconn.side_effect = [dead1, dead2, dead1]
        self.assertFalse(st.save_prediction(PRED))

    def test_rollback_failure_does_not_escape(self) -> None:
        """죽은 연결에서 rollback 도 터지는 경우(원래 사고) — 그래도 False 로 끝난다."""
        st = _storage()
        dead = _FakeConn(
            execute_exc=psycopg2.OperationalError("server closed the connection unexpectedly"),
            rollback_exc=psycopg2.InterfaceError("connection already closed"),
        )
        st._pool.getconn.return_value = dead
        self.assertFalse(st.save_prediction(PRED))

    def test_closed_pool_connection_is_discarded(self) -> None:
        """풀에 닫힌 연결이 남아 있으면 버리고 새 연결을 받는다."""
        st = _storage()
        closed = _FakeConn()
        closed.closed = 1
        fresh = _FakeConn()
        st._pool.getconn.side_effect = [closed, fresh]
        self.assertTrue(st.save_prediction(PRED))
        self.assertIn(mock.call(closed, close=True), st._pool.putconn.call_args_list)


    def test_pool_return_cleans_open_transaction(self) -> None:
        """풀 반납 전에 트랜잭션을 정리한다(idle in transaction 방지).

        2026-09-28 실측: 읽기만 한 연결이 정리 없이 풀에 돌아가 21시간 idle in transaction 으로
        남아 PgIdleInTransactionTooLong 알림이 났다. _put_conn 이 rollback 을 보장해야 한다.
        """
        st = _storage()
        conn = _FakeConn()
        st._put_conn(conn)
        self.assertGreaterEqual(conn.rollbacks, 1, "반납 전 rollback 이 없다(idle in transaction 재발)")
        # 죽은 연결은 rollback 없이 close=True 로 버린다
        dead = _FakeConn()
        st._put_conn(dead, broken=True)
        self.assertEqual(dead.rollbacks, 0)
        st._pool.putconn.assert_called_with(dead, close=True)


if __name__ == "__main__":
    unittest.main()
