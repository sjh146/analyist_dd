"""kis_supply_backfill DB 재연결 회귀 테스트 (DB·네트워크 없음).

왜 필요한가 (실측 2026-11-17 19:10 실행): 수급 러너가 800종목 중 260종목을 적재한 20:00 에
postgres 컨테이너가 재시작(`StartedAt 2026-11-17T11:00:16Z`)하자 남은 종목이 전부
`InterfaceError: connection already closed` 로 실패했고(로그 11줄), 요약 조회
(`conn.cursor()`)에서 traceback 으로 죽어 **'완료:' 줄도 자기신고도 남기지 못했다** —
크론 로그 꼬리에 traceback 만 남아 원인이 보이지 않았다.

규칙:
  · DB 가 죽으면 **재연결하고 계속 수집**한다(진행파일에 안 적은 종목은 다음 실행이 이어받는다).
  · 재연결이 불가능하면 traceback 이 아니라 rc=3(다음 실행에서 재개)으로 끝난다.
  · 요약·자기신고도 재기동에 걸리므로 재연결 후 재시도하고, 그래도 안 되면 자기신고만 생략한다.
"""
import os
import sys
import types
from datetime import date

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import kis_supply_backfill as ks  # noqa: E402


# ── 가짜 커넥션 ────────────────────────────────────────────────────────────
class _Cur:
    def __init__(self, conn):
        self._conn = conn
        self._result = None

    def execute(self, sql, params=None):
        self._conn.executed.append(sql)
        if self._conn.fail_queries:
            raise psycopg2.InterfaceError("connection already closed")
        s = " ".join(sql.split())
        if s.upper().startswith("SELECT COUNT(*) FROM"):
            self._result = (self._conn.row_count,)
        elif "FROM foreign_institutional" in s:
            self._result = (3, self._conn.row_count, date(2026, 8, 14), date(2026, 11, 17))
        elif "FROM ownership" in s:
            self._result = (3, 3, date(2026, 9, 23), date(2026, 11, 17), 3)
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def fetchall(self):
        return []

    def close(self):
        pass


class _Conn:
    def __init__(self, row_count=10, fail_queries=False):
        self.row_count = row_count
        self.fail_queries = fail_queries
        self.executed = []
        self.closed = 0

    def cursor(self):
        if self.closed:
            raise psycopg2.InterfaceError("connection already closed")
        return _Cur(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = 1


class _FakeColl:
    """save_flows 가 지정한 종목에서만 DB 가 죽은 척한다."""

    instances = []

    def __init__(self, client, conn):
        self._conn = conn
        self.last_dropped = 0
        self.last_kept_unloaded = 0
        self.saved = []
        _FakeColl.instances.append(self)

    def ensure_tables(self):
        pass

    def save_flows(self, code, rows):
        if code in _FakeColl.die_on:
            _FakeColl.die_on.remove(code)
            raise psycopg2.InterfaceError("connection already closed")
        self.saved.append(code)
        return len(rows)

    def collect_ownership(self, code, trade_date, excd="J"):
        return 1


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    def get_investor_flow_all(self, code, excd, d_from, d_to, max_pages=None):
        return [{"stck_bsop_date": "20261117", "foreign_net_buy": 1}]


@pytest.fixture
def env(monkeypatch):
    """main() 의 외부 의존(DB·KIS·시장 유니버스)을 전부 스텁으로 바꾼다."""
    conns = []

    def _connect(**kwargs):
        # 첫 연결은 성공하고 **재연결만** 실패하게 만든다(서버가 죽은 뒤 복구 불가 시나리오).
        if _FakeColl.connect_fails and conns:
            raise psycopg2.OperationalError("could not connect to server")
        c = _Conn()
        conns.append(c)
        return c

    shim = types.SimpleNamespace(connect=_connect,
                                 InterfaceError=psycopg2.InterfaceError,
                                 OperationalError=psycopg2.OperationalError)
    monkeypatch.setattr(ks, "psycopg2", shim)
    monkeypatch.setattr(ks, "Config", lambda: types.SimpleNamespace(
        KIS_APP_KEY="k", KIS_APP_SECRET="s", KIS_BASE_URL="http://x", KIS_TOKEN_PATH="/tmp/t"))
    monkeypatch.setattr(ks, "SupplyClient", _FakeClient)
    monkeypatch.setattr(ks, "SupplyCollector", _FakeColl)
    monkeypatch.setattr(ks, "parse_investor_flow", lambda d: d["output"])
    monkeypatch.setattr(ks, "latest_trading_date", lambda conn: date(2026, 11, 17))
    monkeypatch.setattr(ks, "resolve_universe",
                        lambda conn, args: [("A", "a"), ("B", "b"), ("C", "c")])
    monkeypatch.setattr(ks, "staleness_order", lambda conn, todo: todo)
    monkeypatch.setattr(ks, "load_done", lambda p: set())
    monkeypatch.setattr(ks, "save_done", lambda p, code: saved.append(code))
    monkeypatch.setattr(ks.time, "sleep", lambda s: None)
    saved = []
    _FakeColl.instances = []
    _FakeColl.die_on = set()
    _FakeColl.connect_fails = False
    # 자기신고는 dq_claim 실물 대신 스텁(여기서 검증 대상이 아니다).
    monkeypatch.setitem(sys.modules, "dq_claim",
                        types.SimpleNamespace(record_claim=lambda *a, **k: None))
    monkeypatch.setattr(sys, "argv", ["kis_supply_backfill.py", "--top", "3"])
    return types.SimpleNamespace(conns=conns, saved=saved)


def test_db_restart_mid_run_reconnects_and_continues(env, capsys):
    _FakeColl.die_on = {"B"}
    rc = ks.main()
    out = capsys.readouterr().out
    assert rc == 3, out                     # 재연결 후 계속 수집, rc=3 = '다음 실행에서 재개'
    assert env.conns, "재연결이 일어나지 않았다"
    assert "DB 연결 끊김" in out and "재연결 완료" in out
    assert "완료:" in out                   # 요약까지 도달(종전엔 traceback)
    coll = _FakeColl.instances[0]
    assert coll.saved == ["A", "C"]          # B 는 건너뛰고 계속 수집
    assert env.saved == ["A", "C"]           # 진행파일에도 B 는 없다(다음 실행이 재개)
    assert coll._conn is env.conns[-1]       # 저장소가 새 커넥션으로 재바인딩됐다


def test_reconnect_impossible_returns_3(env, capsys):
    _FakeColl.die_on = {"A"}                 # 첫 종목부터 DB 사망
    _FakeColl.connect_fails = True           # 재연결도 불가
    rc = ks.main()
    out = capsys.readouterr().out
    assert rc == 3, out                      # traceback(rc=1) 이 아니라 '다음 실행에서 재개'
    assert "DB 재연결 실패" in out and "실행 중단" in out


def test_summary_reconnects_after_db_restart(env, capsys, monkeypatch):
    """수집은 끝났는데 집계 시점에 커넥션이 죽은 경우 — 재연결 후 요약을 남긴다."""
    real_summary = ks._summary_counts
    calls = {"n": 0}

    def flaky(conn):
        calls["n"] += 1
        if calls["n"] == 1 and not conn.closed:
            raise psycopg2.InterfaceError("connection already closed")
        return real_summary(conn)

    monkeypatch.setattr(ks, "_summary_counts", flaky)
    rc = ks.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "집계 질의 DB 연결 끊김" in out
    assert "완료: ok=3" in out               # 종전: traceback 으로 자기신고 소실
    assert "foreign_institutional:" in out


def test_claims_db_error_does_not_crash(capsys):
    dead = _Conn(fail_queries=True)
    failed = ks._record_claims(dead, {"foreign_institutional": 1, "ownership": 1},
                               flow_rows=5, own_rows=3, src_rows=8,
                               ok=3, fail=0, nodata=0, calls=7)
    out = capsys.readouterr().out
    assert failed is False
    assert "자기신고[foreign_institutional] 생략" in out
