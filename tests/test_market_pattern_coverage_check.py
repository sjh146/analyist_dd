"""R41 — 시장패턴 원장 커버리지 검사. '그날 시장의 21%만 본 값'이 정상값처럼 기록되던 경로.

WHY (실측 2026-11-13): KRX 이름해석 실패 → KIS 폴백(3.5h) 중에 19:40 크론이 돌아
원장에 scored=819(전일 3828)·trade_value 12.25조(전일 22.53조)가 기록됐다. 재실행으로
scored=3805·29.32조로 정정됐다 — 즉 '실행 시각'이 값의 품질을 결정하는데 아무 표시가 없었다.

WHY 확장 (실측 2026-11-16): 종전 검사는 마지막 레코드 하나만 봐서 ① 원장에 아예 없는 세션
(10-12~11-06 20세션 + 11-12 = 21세션 누락) ② scored 는 맞는데 trade_value_sum 만 0 인 행(11-10)을
둘 다 통과시켰다. 아래 테스트는 그 두 침묵을 고정한다.
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import market_pattern_coverage_check as chk  # noqa: E402


def _write(tmp_path, records):
    p = tmp_path / "ledger.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(p)


def _db(*rows):
    """(session, 마감봉수, 거래대금 합) 튜플 목록으로 DB 를 대신한다."""
    return list(rows)


@pytest.fixture(autouse=True)
def _fake_db(monkeypatch):
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: _db(("2026-11-13", 3828, 37.18e12)))


def test_partial_record_is_defect(tmp_path, monkeypatch, capsys):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 819, "trade_value_sum": 12.25e12}])
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: _db(("2026-11-13", 3722, 12.25e12)))
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "공백 1건" in out
    assert "ratio=0.220" in out, "실측값(819/3722)을 그대로 고정한다"


def test_trade_value_zero_is_defect_even_when_scored_ok(tmp_path, capsys):
    """실측 11-10: scored 3726 은 정상인데 trade_value_sum 만 0 이었다(종전 검사 침묵)."""
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3722, "trade_value_sum": 0.0}])
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "거래대금" in out and "공백 1건" in out


def test_trade_value_null_does_not_crash(tmp_path, capsys):
    """원장 구버전 행은 trade_value_sum 키가 없다 — 검사가 traceback 으로 죽으면 판정이 사라진다."""
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3828}])
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "tv=null" in out and "공백 1건" in out


def test_missing_session_is_defect(tmp_path, monkeypatch, capsys):
    """실측 10-12~11-06: 원장에 아예 없는 세션은 scored 비교 대상조차 없었다(종전 침묵)."""
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3722, "trade_value_sum": 37.18e12}])
    monkeypatch.setattr(chk, "db_sessions",
                        lambda limit=30: _db(("2026-11-12", 3723, 21.28e12),
                                             ("2026-11-13", 3722, 37.18e12)))
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "누락" in out and "2026-11-12" in out and "공백 1건" in out


def test_complete_record_passes(tmp_path, monkeypatch, capsys):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3805, "trade_value_sum": 29.32e12}])
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: _db(("2026-11-13", 3828, 29.32e12)))
    rc = chk.main(["--ledger", p])
    assert rc == 0 and "충족" in capsys.readouterr().out


def test_tolerance_band_is_not_a_defect(tmp_path):
    # 90% 이상이면 정상(상장폐지·거래정지·신규상장 차이)
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3450, "trade_value_sum": 37.18e12}])
    assert chk.main(["--ledger", p]) == 0


def test_just_below_tolerance_is_defect(tmp_path):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3400, "trade_value_sum": 37.18e12}])
    assert chk.main(["--ledger", p]) == 1


def test_last_record_wins_when_duplicated(tmp_path):
    # 원장은 세션별 덮어쓰기 — 뒤에 나온 줄이 이긴다
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 819, "trade_value_sum": 0},
                          {"session": "2026-11-13", "scored": 3805, "trade_value_sum": 37.18e12}])
    assert chk.main(["--ledger", p]) == 0


def test_missing_ledger_is_unjudgeable(tmp_path):
    assert chk.main(["--ledger", str(tmp_path / "nope.jsonl")]) == 2


def test_empty_ledger_is_unjudgeable(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    assert chk.main(["--ledger", str(p)]) == 2


def test_db_failure_is_unjudgeable_not_pass(tmp_path, monkeypatch):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3805, "trade_value_sum": 37.18e12}])

    def boom(limit=30):
        raise RuntimeError("db down")

    monkeypatch.setattr(chk, "db_sessions", boom)
    assert chk.main(["--ledger", p]) == 2, "판정 불가는 0(통과)이 아니다"


def test_empty_db_window_is_unjudgeable(tmp_path, monkeypatch):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3805, "trade_value_sum": 37.18e12}])
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: [])
    assert chk.main(["--ledger", p]) == 2


def test_last_number_in_stdout_is_defect_count(tmp_path, monkeypatch, capsys):
    """구동기 eval_check 는 stdout 의 **마지막 수치**를 check_value 로 읽는다."""
    import re
    p = _write(tmp_path, [{"session": "2026-11-12", "scored": 900, "trade_value_sum": 0}])
    monkeypatch.setattr(chk, "db_sessions",
                        lambda limit=30: _db(("2026-11-12", 3723, 21.28e12),
                                             ("2026-11-13", 3722, 37.18e12)))
    chk.main(["--ledger", p])
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", capsys.readouterr().out)
    # 결함 3 = 11-12 부분기록 + 11-12 거래대금 + 11-13 누락
    assert float(nums[-1]) == 3
