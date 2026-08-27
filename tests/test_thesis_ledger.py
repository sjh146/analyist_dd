# -*- coding: utf-8 -*-
"""
test_thesis_ledger.py — 07_thesis_ledger.sql 정적 검증 스위트 (DB 무의존).

이 스위트는 init-scripts/postgres/07_thesis_ledger.sql 파일을 문자열로 읽어
내용(테이블/컬럼/제약조건/인덱스/append-only 트리거)을 단언한다.
데이터베이스 연결이나 애플리케이션 코드 import 없이, SQL 파일의 구조를
고정(freeze)하여 향후 수정이 제약조건·트리거를 조용히 제거하지 못하게 한다.
"""
import re
from pathlib import Path

_SQL = Path(__file__).resolve().parents[1] / "init-scripts" / "postgres" / "07_thesis_ledger.sql"


def _sql_text() -> str:
    """SQL 파일 전체를 UTF-8 문자열로 반환한다."""
    return _SQL.read_text(encoding="utf-8")


def test_sql_file_exists():
    assert _SQL.exists()
    assert _SQL.is_file()
    assert len(_sql_text().strip()) > 0


def test_position_theses_defined():
    assert "CREATE TABLE IF NOT EXISTS position_theses" in _sql_text()


def test_position_theses_columns():
    text = _sql_text()
    fragments = [
        "stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code)",
        "thesis_text TEXT NOT NULL",
        "disproof_criteria TEXT NOT NULL",
        "strategy_name VARCHAR(50) NOT NULL DEFAULT 'ackman_fundamental'",
        "status VARCHAR(20) NOT NULL DEFAULT 'active'",
        "intrinsic_value DECIMAL(20,4)",
        "entry_price DECIMAL(20,4)",
        "catalyst_events JSONB",
        "decision_log JSONB",
    ]
    for frag in fragments:
        assert frag in text


def test_thesis_verdicts_defined():
    assert "CREATE TABLE IF NOT EXISTS thesis_verdicts" in _sql_text()


def test_thesis_verdicts_columns():
    text = _sql_text()
    fragments = [
        "thesis_id INT NOT NULL REFERENCES position_theses(id)",
        "verdict_date DATE NOT NULL",
        "verdict VARCHAR(20) NOT NULL",
        "verdict_score DECIMAL(5,4)",
        "evidence_event_ids INT[]",
        "evidence_summary TEXT",
        "model_version VARCHAR(50)",
    ]
    for frag in fragments:
        assert frag in text


def test_verdict_whitelist_check():
    text = _sql_text()
    assert "chk_thesis_verdicts_verdict" in text
    start = text.index("chk_thesis_verdicts_verdict")
    end = text.index("chk_thesis_verdicts_score")
    verdict_check = text[start:end]
    for verdict in ("강화", "유지", "약화", "손상", "파기"):
        assert verdict in verdict_check


def test_verdict_score_check():
    text = _sql_text()
    assert "chk_thesis_verdicts_score" in text
    start = text.index("chk_thesis_verdicts_score")
    score_check = text[start:text.index(");", start)]
    assert "-1.0" in score_check
    assert "1.0" in score_check


def test_status_check():
    text = _sql_text()
    assert "chk_position_theses_status" in text
    start = text.index("chk_position_theses_status")
    status_check = text[start:text.index(");", start)]
    for status in ("active", "exited", "thesis_broken"):
        assert status in status_check


def test_unique_thesis_verdict_date():
    assert "UNIQUE(thesis_id, verdict_date)" in _sql_text()


def test_verdict_index():
    fragment = (
        "CREATE INDEX IF NOT EXISTS idx_thesis_verdicts_thesis\n"
        "    ON thesis_verdicts(thesis_id, verdict_date DESC)"
    )
    assert fragment in _sql_text()


def test_thesis_status_index():
    assert "CREATE INDEX IF NOT EXISTS idx_position_theses_status" in _sql_text()


def test_append_only_trigger():
    text = _sql_text()
    assert "BEFORE UPDATE OR DELETE ON thesis_verdicts" in text
    assert "RAISE EXCEPTION" in text
    assert "CREATE OR REPLACE FUNCTION forbid_thesis_verdicts_mutation" in text
    assert "CREATE TRIGGER trg_thesis_verdicts_no_update" in text


def test_no_update_delete_statements():
    text = _sql_text()
    # UPDATE/DELETE 는 트리거 정의("BEFORE UPDATE OR DELETE")·주석·문자열
    # 리터럴에서만 등장할 수 있고, 데이터 변형 "문장"으로는 존재해서는 안 된다.
    assert "\nUPDATE " not in text
    assert "\nDELETE " not in text
    for line in text.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("UPDATE ")
        assert not stripped.startswith("DELETE ")


def test_idempotent():
    text = _sql_text()
    assert text.count("CREATE TABLE IF NOT EXISTS") >= 2
    assert text.count("CREATE INDEX IF NOT EXISTS") >= 3
    assert re.search(r"CREATE TABLE (?!IF NOT EXISTS)", text) is None
