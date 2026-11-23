"""data_gap check 모드 — 감시 check 가 '그날 한 점'이 아니라 '공실 수' 를 내도록 (DB·네트워크 없음).

왜 필요한가 (실측 2026-11-10)
- R35 check 는 '11-06 종목수 ≥ 3,600' 이었다 → 하루만 지나도 영원히 참이고, 중간 날짜 공백은
  구조적으로 못 잡는다(감시가 아니라 일회성 판정). 감시 check 는 '결함이 있으면 >0, 고치면 0' 이어야 한다.
- stale(창 밖 공백)을 0 으로 내보내면 실명이 '정상'으로 보고된다 → 999(=실패)로 내보낸다.
"""
import os
import subprocess
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import data_gap as dg  # noqa: E402


def _capture(capsys):
    out = capsys.readouterr()
    return out.out.strip(), out.err.strip()


def test_clean_state_prints_zero(monkeypatch, capsys):
    monkeypatch.setattr(dg, "effective_lookback", lambda: 10)
    monkeypatch.setattr(dg, "stale_warning", lambda *a, **k: (False, ""))
    monkeypatch.setattr(dg, "find_gaps", lambda **k: ([], set()))
    assert dg.cmd_check() == 0
    out, _err = _capture(capsys)
    assert out == "0"


def test_gap_days_are_counted(monkeypatch, capsys):
    monkeypatch.setattr(dg, "effective_lookback", lambda: 10)
    monkeypatch.setattr(dg, "stale_warning", lambda *a, **k: (False, ""))
    days = [(date.today() - timedelta(days=i)).isoformat() for i in (2, 3, 4)]
    monkeypatch.setattr(dg, "find_gaps", lambda **k: ([(d, 0) for d in days], set()))
    assert dg.cmd_check() == 0
    out, err = _capture(capsys)
    assert out == "3", "공실 3일이면 check 값은 3 이어야 한다"
    assert "공실:" in err, "어느 날짜인지 stderr 에 남아야 한다"


def test_stale_reports_failure_not_silence(monkeypatch, capsys):
    """창 90일 밖 공백 = 판정 불가 → 0(정상)으로 내보내면 안 된다."""
    monkeypatch.setattr(dg, "effective_lookback", lambda: 90)
    monkeypatch.setattr(dg, "stale_warning", lambda *a, **k: (True, "최신 적재 오래됨"))
    assert dg.cmd_check() == 0
    out, err = _capture(capsys)
    assert out == "999"
    assert "stale" in err


def test_check_mode_is_wired_in_main():
    """main 이 check 모드를 모르면 usage(2)로 끝난다 — 실제 프로세스로 배선을 본다."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, os.path.join(root, "scripts", "data_gap.py"), "check"],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode != 2, "check 모드가 usage(2)로 끝남: {0}".format(proc.stdout[:200])
    assert "usage:" not in proc.stdout


def test_unknown_mode_exits_usage():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, os.path.join(root, "scripts", "data_gap.py"), "bogus-mode"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2
    assert "usage:" in proc.stdout
