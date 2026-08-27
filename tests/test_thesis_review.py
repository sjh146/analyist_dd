"""thesis_review DB-free unit tests (mock — FakeConn/FakeCursor + SQL 프래그먼트 디스패치).

플랜 §테스트 설계 10개: DB·외부 API·LLM·Discord 실호출 0.
sys.path.insert(0, scripts) + `import thesis_review as tr` (test_thesis_onboarding.py 관례).
thesis_review는 thesis_onboarding을 import하므로 scripts 경로 삽입이 선행되어야 한다.
"""
import json
import os
import sys
from argparse import Namespace

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import thesis_review as tr  # noqa: E402


# ── 픽스처 (position_theses 12컬럼 / thesis_verdicts 5컬럼 튜플 행) ──────────


def _thesis_row(thesis_id, code, status="active", entry_price=120000):
    """get_thesis SELECT shape (컬럼 12개 명시) 1행 튜플."""
    return (thesis_id, code, "ackman_fundamental",
            f"사업: {code} / 왜 좋은가: 성장 / 본질가치: 150,000원 기준",
            "반박증거 확인 시 즉시 매도",
            150000, entry_price,
            [{"event_type": "공시", "desc": "증설", "deadline": "2027-06-30"}],
            status,
            [{"ts": "2026-08-28T09:00:00", "type": "onboarding", "approval_id": None}],
            "2026-08-28 09:00:00", "2026-08-28 09:00:00")


def _verdict_row(thesis_id, verdict_date, verdict, score=0.25, summary="근거"):
    """get_thesis_verdicts SELECT shape (verdict_date, verdict, score, summary, model)"""
    return (thesis_id, verdict_date, verdict, score, summary, "thesis-judge-v1")


THEMES = [_thesis_row(1, "005930", "active"), _thesis_row(4, "000660", "exited")]
VERDICTS = [
    _verdict_row(1, "2026-08-27", "강화", 0.25, "HBM 수주 확대"),
    _verdict_row(1, "2026-08-26", "유지", 0.0, "관련 이벤트 없음"),
]

# rewrite monkeypatch용 고정 행 dict (draft_thesis 대체 — LLM/HTTP 0건)
FIXED_REWRITE_ROW = {
    "stock_code": "257720",
    "strategy_name": "ackman_fundamental",
    "thesis_text": "사업: 이차전지 북미 / 왜 좋은가: 증설 / 본질가치: 160,000원 기준",
    "disproof_criteria": "수주 취소 시 매도",
    "intrinsic_value": 160000,
    "entry_price": 120000,
    "catalyst_events": [],
    "decision_log": [{"ts": "2026-08-28T09:00:00", "type": "onboarding", "approval_id": None}],
}


# ── Fake DB (SQL 프래그먼트 디스패치 — Metis m5 / test_thesis_onboarding 관례) ─


class ReviewCursor:
    """SQL 프래그먼트별 행 디스패치 + last_sql/last_params/rowcount 기록."""

    def __init__(self, conn):
        self.conn = conn
        self.last_sql = None
        self.last_params = None
        self.rowcount = 0
        self._rows = []

    def _group_counts(self, rows, idx):
        counts = {}
        for r in rows:
            counts[r[idx]] = counts.get(r[idx], 0) + 1
        return list(counts.items())

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
        self.conn.executed.append((sql, params))
        self.conn.last_cursor = self
        self._rows = []
        self.rowcount = 0
        theses = self.conn.theses
        verdicts = self.conn.verdicts
        if "INSERT INTO position_theses" in sql:
            self._rows = [(self.conn.new_id,)]
            self.rowcount = 1
        elif "UPDATE position_theses SET status" in sql:
            self.rowcount = self.conn.exit_rowcount
        elif "UPDATE position_theses SET decision_log" in sql:
            self.rowcount = self.conn.link_rowcount
        elif "SELECT stock_name, sector FROM stocks" in sql:
            self._rows = [("실리콘투", "Unknown")]
        elif "GROUP BY status" in sql:
            self._rows = self._group_counts(theses, 8)
        elif "GROUP BY verdict" in sql:
            self._rows = self._group_counts(verdicts, 2)
        elif "COUNT(*) FROM thesis_verdicts" in sql:
            self._rows = [(len(verdicts),)]
        elif "FROM thesis_verdicts WHERE thesis_id" in sql:
            tid = params[0]
            self._rows = [
                (v[1], v[2], v[3], v[4], v[5]) for v in verdicts if v[0] == tid
            ]
            limit = params[1] if params and len(params) > 1 else None
            if limit is not None:
                self._rows = self._rows[:limit]
        elif "FROM position_theses WHERE id = %s" in sql:
            tid = params[0]
            self._rows = [t for t in theses if t[0] == tid]
        elif "WHERE status = 'active' ORDER BY id" in sql:
            self._rows = [(t[0], t[1]) for t in theses if t[8] == "active"]
        else:  # 목록: SELECT id, stock_code, status, created_at ... ORDER BY id
            self._rows = [(t[0], t[1], t[8], t[10]) for t in theses]

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class ReviewConn:
    """ReviewCursor 발급 FakeConn — executed 로그 + commit/rollback/rowcount 추적."""

    def __init__(self, theses=(), verdicts=(), new_id=5, exit_rowcount=1, link_rowcount=1):
        self.theses = list(theses)
        self.verdicts = list(verdicts)
        self.new_id = new_id
        self.exit_rowcount = exit_rowcount
        self.link_rowcount = link_rowcount
        self.executed = []
        self.last_cursor = None
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        self.last_cursor = ReviewCursor(self)
        return self.last_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


# ── 1~3. 조회 명령 ──────────────────────────────────────────────────────────


def test_list_output(capsys):
    """Given: 테제 2행 + 판정 1행 — When: --list — Then: 콘솔에 종목/상태/최근 판정."""
    conn = ReviewConn(theses=THEMES, verdicts=VERDICTS)
    tr._cmd_list(Namespace(), conn)
    out = capsys.readouterr().out
    assert "005930" in out and "000660" in out
    assert "active" in out and "exited" in out
    assert "강화" in out  # 최근 판정 요약 컬럼


def test_status_detail(capsys):
    """Given: 테제 1 + 판정 2행 — When: --status 1 — Then: 판정 이력 전체 포함."""
    conn = ReviewConn(theses=THEMES, verdicts=VERDICTS)
    tr._cmd_status(Namespace(status=1), conn)
    out = capsys.readouterr().out
    assert "판정 이력 (thesis_verdicts, 2건)" in out
    assert "강화" in out and "유지" in out


def test_report_summary(capsys):
    """Given: 테제 2행(active/exited) + 판정 2행 — When: --report — Then: 집계 출력."""
    conn = ReviewConn(theses=THEMES, verdicts=VERDICTS)
    tr._cmd_report(Namespace(), conn)
    out = capsys.readouterr().out
    assert "테제원장 요약" in out
    assert "강화: 1" in out and "유지: 1" in out
    executed = [sql for sql, _ in conn.executed]
    assert any("GROUP BY verdict" in s for s in executed)


# ── 4~6. exit ───────────────────────────────────────────────────────────────


def test_exit_sql_appends_decision_log():
    """Given: exit_thesis 실행 — When: UPDATE — Then: append SQL + exit JSON + commit."""
    conn = ReviewConn(theses=THEMES)
    updated = tr.exit_thesis(conn, 1, "테제 달성")
    cur = conn.last_cursor
    assert updated == 1
    assert "decision_log = decision_log || %s::jsonb" in cur.last_sql
    assert "status = 'exited'" in cur.last_sql
    assert "status != 'exited'" in cur.last_sql
    assert cur.last_params[1] == 1
    entry = json.loads(cur.last_params[0])
    assert entry[0]["type"] == "exit" and entry[0]["reason"] == "테제 달성"
    assert conn.commits == 1 and conn.rollbacks == 0


def test_exit_unknown_id_error():
    """Given: UPDATE rowcount 0 — When: --exit 1 — Then: 오류 + 비제로 exit."""
    conn = ReviewConn(theses=THEMES, exit_rowcount=0)
    with pytest.raises(SystemExit) as ei:
        tr._cmd_exit(Namespace(exit_id=1, reason="재해지"), conn)
    assert ei.value.code == 1


def test_exit_already_exited_error():
    """Given: status='exited' 행(id=4) — When: --exit 4 — Then: 오류 + UPDATE 0건."""
    conn = ReviewConn(theses=THEMES)
    with pytest.raises(SystemExit) as ei:
        tr._cmd_exit(Namespace(exit_id=4, reason="중복 해지"), conn)
    assert ei.value.code == 1
    executed = [s for s, _ in conn.executed]
    assert not any("UPDATE" in s for s in executed)


# ── 7~9. rewrite ────────────────────────────────────────────────────────────


def test_rewrite_dry_run_no_insert(capsys, monkeypatch):
    """Given: draft 고정 행 — When: --rewrite 1 --dry-run — Then: UPDATE/INSERT 0건."""
    monkeypatch.setattr(tr, "draft_thesis", lambda candidate, pg: dict(FIXED_REWRITE_ROW))
    conn = ReviewConn(theses=THEMES)
    tr._cmd_rewrite(Namespace(rewrite_id=1, reason="실적 구조 변화", dry_run=True), conn)
    out = capsys.readouterr().out
    assert "[--dry-run]" in out
    executed = [s for s, _ in conn.executed]
    assert not any("UPDATE" in s or "INSERT INTO" in s for s in executed)
    assert conn.commits == 0 and conn.rollbacks == 0


def test_rewrite_happy(monkeypatch):
    """Given: draft 고정 행 — When: --rewrite 1 — Then: exit→INSERT→link 순서·파라미터."""
    monkeypatch.setattr(tr, "draft_thesis", lambda candidate, pg: dict(FIXED_REWRITE_ROW))
    conn = ReviewConn(theses=THEMES, new_id=5)
    new_id = tr.rewrite_thesis(conn, tr.get_thesis(conn, 1), "실적 구조 변화")
    assert new_id == 5

    stmts = list(conn.executed)
    exit_idx = next(i for i, (s, _) in enumerate(stmts) if "SET status" in s)
    ins_idx = next(i for i, (s, _) in enumerate(stmts) if "INSERT INTO position_theses" in s)
    link_idx = next(i for i, (s, _) in enumerate(stmts) if "SET decision_log" in s)
    assert exit_idx < ins_idx < link_idx

    exit_sql, exit_params = stmts[exit_idx]
    assert "decision_log = decision_log || %s::jsonb" in exit_sql
    exit_entry = json.loads(exit_params[0])[0]
    assert exit_entry["type"] == "rewrite" and exit_entry["old_id"] == 1
    assert exit_entry["new_id"] is None and exit_entry["reason"] == "실적 구조 변화"

    ins_sql, ins_params = stmts[ins_idx]
    assert "'active'" in ins_sql
    ins_log = json.loads(ins_params[7])[0]
    assert ins_log["type"] == "onboarding" and ins_log["source"] == "quarterly_review"
    assert ins_log["old_id"] == 1
    assert ins_params[5] == 120000  # 기존 행 entry_price 유지

    link_sql, link_params = stmts[link_idx]
    assert "status" not in link_sql.split("SET")[1].split("WHERE")[0]  # status 불변
    link_entry = json.loads(link_params[0])[0]
    assert link_entry["type"] == "rewrite_link" and link_entry["new_id"] == 5
    assert link_entry["old_id"] == 1 and link_entry["reason"] == "실적 구조 변화"
    assert conn.commits == 1 and conn.rollbacks == 0


def test_rewrite_rollback_on_draft_failure(monkeypatch):
    """Given: 초안 생성 None — When: --rewrite 1 — Then: ROLLBACK + INSERT 0건 + 기존 행 유지."""
    monkeypatch.setattr(tr, "draft_thesis", lambda candidate, pg: None)
    conn = ReviewConn(theses=THEMES)
    with pytest.raises(SystemExit) as ei:
        tr._cmd_rewrite(Namespace(rewrite_id=1, reason="실적 구조 변화", dry_run=False), conn)
    assert ei.value.code == 1
    assert conn.rollbacks == 1 and conn.commits == 0
    stmts = [s for s, _ in conn.executed]
    assert sum("INSERT INTO" in s for s in stmts) == 0
    assert sum("SET status" in s for s in stmts) == 1  # exit UPDATE 실행 후 롤백(미커밋)


# ── 10. 판정 원장 무접촉 (정적 단언) ─────────────────────────────────────────


def test_review_never_touches_verdicts():
    """When: 모듈 소스 정적 검사 — Then: thesis_verdicts 쓰기 구문 0건 (조회만)."""
    with open(os.path.join(REPO_ROOT, "scripts", "thesis_review.py"),
              encoding="utf-8") as f:
        src = f.read()
    for write in ("INSERT INTO thesis_verdicts", "UPDATE thesis_verdicts",
                  "DELETE FROM thesis_verdicts"):
        assert write not in src
    assert "FROM {VERDICTS_TABLE}" in src  # SELECT 조회 경로만 존재
