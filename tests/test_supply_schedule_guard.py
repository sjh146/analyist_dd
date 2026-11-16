"""수급 수집 스케줄 감시(supply_schedule_guard) 회귀 테스트.

실측 배경: root crontab 소실로 kis_supply.sh 가 11-14 이후 정지했는데 어떤 지표도
'스케줄이 없다'를 말하지 않았다 → 스케줄 존재 자체를 수치(결함 수)로 판정한다.
"""
import importlib.util
import os
import sys

import pytest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_guard():
    path = os.path.join(PROJ, "scripts", "supply_schedule_guard.py")
    spec = importlib.util.spec_from_file_location("supply_schedule_guard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOOD_CRON = "*/5 * * * * /home/jhshi/cron/newsgate_keepalive.sh\n"
GOOD_SUPPLY = "30 19 * * 1-5 /home/jhshi/cron/kis_supply.sh >> x.log 2>&1\n"
GOOD_PS = "   946 ?  Ss  0:00 /usr/sbin/cron -P\n"


def test_ok_when_schedule_and_daemon_present():
    g = load_guard()
    assert g.defects(GOOD_CRON + GOOD_SUPPLY, GOOD_PS) == []


def test_missing_schedule_is_a_defect():
    g = load_guard()
    bad = g.defects(GOOD_CRON, GOOD_PS)
    assert len(bad) == 1
    assert "kis_supply.sh" in bad[0]


def test_missing_daemon_is_a_defect():
    g = load_guard()
    bad = g.defects(GOOD_CRON + GOOD_SUPPLY, " 1234 ? Ss python3 other.py\n")
    assert len(bad) == 1
    assert "cron 데몬" in bad[0]


def test_both_missing_counts_two():
    g = load_guard()
    assert len(g.defects("", "")) == 2


def test_main_prints_defect_count_last(capsys, monkeypatch):
    g = load_guard()
    monkeypatch.setenv("SUPPLY_CRONTAB_TEXT", GOOD_CRON)
    monkeypatch.setenv("SUPPLY_CRON_PS_TEXT", GOOD_PS)
    assert g.main() == 0
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "cron_defects=1"


def test_main_ok_path(capsys, monkeypatch):
    g = load_guard()
    monkeypatch.setenv("SUPPLY_CRONTAB_TEXT", GOOD_CRON + GOOD_SUPPLY)
    monkeypatch.setenv("SUPPLY_CRON_PS_TEXT", GOOD_PS)
    assert g.main() == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "cron_defects=0"
