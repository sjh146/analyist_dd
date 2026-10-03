"""DART 백필 resume 커서 회귀 테스트.

WHY (실측 2026-10-03): 러너는 (타입,창)을 처음부터 훑고 `--max-calls` 에서 멈추는데 커서가 없어
토요일 `--regular`(3개월 × A,B,D,E,I ≫200콜)가 **매주 같은 앞 구간만 다시 받고** 뒤 구간을 영원히
못 채웠다. 커서는 "완료한 (타입,창) 개수"를 저장해 다음 실행이 이어가게 한다.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import datetime as dt  # noqa: E402

import dart_disclosure_backfill as dbf  # noqa: E402


@pytest.fixture()
def cursor(tmp_path, monkeypatch):
    p = tmp_path / "cursor.json"
    monkeypatch.setattr(dbf, "CURSOR_PATH", str(p))
    return p


def test_cursor_roundtrip(cursor):
    pairs = [("A", (dt.date(2026, 7, 1), dt.date(2026, 7, 31))),
             ("A", (dt.date(2026, 8, 1), dt.date(2026, 8, 31)))]
    key = dbf._cursor_key(pairs)
    assert dbf.load_cursor(key) is None          # 처음엔 커서 없음
    dbf.save_cursor(key, 1, len(pairs))
    assert dbf.load_cursor(key) == 1
    assert json.loads(cursor.read_text(encoding="utf-8"))["total"] == 2


def test_cursor_ignored_when_windows_change(cursor):
    """창·유형이 바뀌면 커서를 무시한다(다른 작업의 진도를 물려받지 않는다)."""
    old = dbf._cursor_key([("A", (dt.date(2026, 7, 1), dt.date(2026, 7, 31)))])
    dbf.save_cursor(old, 1, 1)
    new = dbf._cursor_key([("B", (dt.date(2026, 8, 1), dt.date(2026, 8, 31)))])
    assert dbf.load_cursor(new) is None


def test_clear_cursor(cursor):
    key = dbf._cursor_key([("A", (dt.date(2026, 7, 1), dt.date(2026, 7, 31)))])
    dbf.save_cursor(key, 0, 1)
    assert cursor.exists()
    dbf.clear_cursor()
    assert not cursor.exists()
    dbf.clear_cursor()                            # 없는 파일 삭제도 조용히 통과


def test_broken_cursor_file_is_ignored(cursor):
    cursor.write_text("{ 깨진 json", encoding="utf-8")
    assert dbf.load_cursor("anything") is None     # 예외로 죽지 않는다


def test_save_cursor_failure_does_not_raise(cursor, monkeypatch):
    """커서 저장 실패가 수집을 막으면 안 된다(로그만 남긴다)."""
    monkeypatch.setattr(dbf.os, "makedirs",
                        lambda *a, **kw: (_ for _ in ()).throw(PermissionError(13, "denied")))
    dbf.save_cursor("k", 1, 2)                     # 예외 없이 통과
