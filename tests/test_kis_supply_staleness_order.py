"""kis_supply_backfill.staleness_order 회귀 테스트 (DB·네트워크 없음).

왜 필요한가 (실측 2026-09-28): 진행파일이 `supply_progress_<from>_<to>` 키라 `--to`
(=최신거래일)가 바뀌는 날마다 `done` 이 리셋된다. 유니버스 800종목 × 실행당 호출 상한
500 이면 매 실행이 **유니버스 앞 250종목만** 갱신하고 끝나 꼬리 종목이 영구히 뒤처졌다
(foreign_institutional 9/28 커버리지 250/542 = 46%, 280종목이 9/23 정지).
정렬 규칙이 깨지면 같은 굶주림이 조용히 재발한다.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import kis_supply_backfill as ks  # noqa: E402


class _StubCursor:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = None
        self.closed = False

    def execute(self, sql, params=None):
        self.last_sql = sql
        # 관측된 최신일만 돌려준다 (테이블에 없는 코드 = 미보유)
        self._result = [r for r in self._rows if r[0] in set(params[0])]

    def fetchall(self):
        return list(self._result)

    def close(self):
        self.closed = True


class _StubConn:
    def __init__(self, rows):
        self._rows = rows
        self.cur = _StubCursor(rows)

    def cursor(self):
        return self.cur


D = date


def test_stale_first():
    """최신일이 오래된 종목이 앞으로 온다."""
    todo = [("A", "f fresh"), ("B", "stale"), ("C", "mid")]
    conn = _StubConn([("A", D(2026, 9, 28)), ("B", D(2026, 9, 23)), ("C", D(2026, 9, 25))])
    out = [c for c, _ in ks.staleness_order(conn, todo)]
    assert out == ["B", "C", "A"]


def test_never_collected_goes_first():
    """테이블에 아예 없는 종목은 가장 오래된 것으로 취급돼 먼저 처리된다."""
    todo = [("A", "f"), ("Z", "never")]
    conn = _StubConn([("A", D(2026, 9, 28))])
    out = [c for c, _ in ks.staleness_order(conn, todo)]
    assert out == ["Z", "A"]


def test_tie_break_is_stable_by_code():
    """같은 최신일이면 코드 오름차순 — 재실행마다 순서가 흔들리지 않는다."""
    todo = [("C", "c"), ("A", "a"), ("B", "b")]
    conn = _StubConn([("A", D(2026, 9, 23)), ("B", D(2026, 9, 23)), ("C", D(2026, 9, 23))])
    assert [c for c, _ in ks.staleness_order(conn, todo)] == ["A", "B", "C"]


def test_empty_todo_short_circuits():
    """todo 가 비면 SELECT 를 치지 않고 그대로 돌려준다(빈 실행 보호)."""
    conn = _StubConn([])
    assert ks.staleness_order(conn, []) == []
    assert conn.cur.last_sql is None


def test_returns_numbered_tuples_not_codes():
    """정렬이 (코드, 종목명) 튜플을 보존해야 저장 단계가 이름을 잃지 않는다."""
    todo = [("A", "삼성전자"), ("B", "SK하이닉스")]
    conn = _StubConn([("A", D(2026, 9, 28)), ("B", D(2026, 9, 23))])
    assert ks.staleness_order(conn, todo) == [("B", "SK하이닉스"), ("A", "삼성전자")]


def test_read_only_sql():
    """정렬 헬퍼는 SELECT 만 한다 — 쓰기 SQL 이 섞이면 조용한 DB 변경이 된다."""
    conn = _StubConn([("A", D(2026, 9, 28))])
    ks.staleness_order(conn, [("A", "a")])
    sql = conn.cur.last_sql.lower()
    assert sql.strip().startswith("select")
    for bad in ("insert", "update", "delete", "alter", "drop", "create"):
        assert bad not in sql
