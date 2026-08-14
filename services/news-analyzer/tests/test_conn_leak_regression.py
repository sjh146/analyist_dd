"""Regression test: DB connection pool must not leak.

2026-08 실측: DataQualityIntegration._fetch_recent_scores 가 풀에서 conn 을
획득하고 반환하지 않아, 배치 중 'connection pool exhausted' 로 파이프라인이
중단되었다. provider/putter 쌍을 주입해 성공/실패 경로 모두 putter 호출을
보장하는지 검증한다.
"""

import logging

from app.data_quality_integration import DataQualityIntegration

logger = logging.getLogger(__name__)

_SENTINEL = object()


class _FakeConn:
    """Cursor 로 행 1개를 반환하거나 raise 하는 가짜 커넥션."""

    def __init__(self, fail: bool = False):
        self.fail = fail

    def cursor(self):
        if self.fail:
            raise RuntimeError("db boom")
        return self

    def execute(self, *a, **k):
        if self.fail:
            raise RuntimeError("db boom")

    def fetchall(self):
        return [(0.5,)]

    def close(self):
        pass


def _make_dq(fail: bool = False, putter=_SENTINEL):
    calls = {"get": 0, "put": 0}

    def provider():
        calls["get"] += 1
        return _FakeConn(fail=fail)

    def default_putter(conn):
        calls["put"] += 1

    effective_putter = default_putter if putter is _SENTINEL else putter
    dq = DataQualityIntegration(
        db_conn_provider=provider,
        db_conn_putter=effective_putter,
    )
    return dq, calls


def test_fetch_recent_scores_returns_conn_on_success():
    dq, calls = _make_dq()
    scores = dq._fetch_recent_scores("005930")
    assert scores == [0.5]
    assert calls["get"] == 1
    assert calls["put"] == 1, "성공 경로에서도 conn 은 풀에 반환되어야 한다"


def test_fetch_recent_scores_returns_conn_on_exception():
    dq, calls = _make_dq(fail=True)
    scores = dq._fetch_recent_scores("005930")
    assert scores == []
    assert calls["get"] == 1
    assert calls["put"] == 1, "예외 경로에서도 conn 은 풀에 반환되어야 한다"


def test_fetch_recent_scores_no_putter_is_safe():
    dq, calls = _make_dq(putter=None)
    scores = dq._fetch_recent_scores("005930")
    assert scores == [0.5]
    assert calls["get"] == 1
    # putter 미주입 시 put 는 0 (크래시 없이 동작)
    assert calls["put"] == 0
