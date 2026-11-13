"""R41 — 시장패턴 원장 커버리지 검사. '그날 시장의 21%만 본 값'이 정상값처럼 기록되던 경로.

WHY (실측 2026-11-13): KRX 이름해석 실패 → KIS 폴백(3.5h) 중에 19:40 크론이 돌아
원장에 scored=819(전일 3828)·trade_value 12.25조(전일 22.53조)가 기록됐다. 재실행으로
scored=3805·29.32조로 정정됐다 — 즉 '실행 시각'이 값의 품질을 결정하는데 아무 표시가 없었다.
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


@pytest.fixture(autouse=True)
def _fake_db(monkeypatch):
    monkeypatch.setattr(chk, "db_rows", lambda session: 3828)


def test_partial_record_is_defect(tmp_path, monkeypatch, capsys):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 819}])
    monkeypatch.setattr(chk, "db_rows", lambda s: 3722)
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "공백 1건" in out
    assert "ratio=0.220" in out, "실측값(819/3722)을 그대로 고정한다"


def test_complete_record_passes(tmp_path, capsys):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3805}])
    rc = chk.main(["--ledger", p])
    assert rc == 0 and "충족" in capsys.readouterr().out


def test_tolerance_band_is_not_a_defect(tmp_path):
    # 90% 이상이면 정상(상장폐지·거래정지·신규상장 차이)
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3450}])
    assert chk.main(["--ledger", p]) == 0


def test_just_below_tolerance_is_defect(tmp_path):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3400}])
    assert chk.main(["--ledger", p]) == 1


def test_last_record_wins(tmp_path):
    # 원장은 세션별 덮어쓰기 — 마지막 줄이 판정 대상이다
    p = _write(tmp_path, [{"session": "2026-11-12", "scored": 9999},
                          {"session": "2026-11-13", "scored": 3805}])
    assert chk.main(["--ledger", p]) == 0


def test_missing_ledger_is_unjudgeable(tmp_path):
    assert chk.main(["--ledger", str(tmp_path / "nope.jsonl")]) == 2


def test_db_failure_is_unjudgeable_not_pass(tmp_path, monkeypatch):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3805}])

    def boom(s):
        raise RuntimeError("db down")

    monkeypatch.setattr(chk, "db_rows", boom)
    assert chk.main(["--ledger", p]) == 2, "판정 불가는 0(통과)이 아니다"


def test_empty_db_session_is_unjudgeable(tmp_path, monkeypatch):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3805}])
    monkeypatch.setattr(chk, "db_rows", lambda s: 0)
    assert chk.main(["--ledger", p]) == 2
