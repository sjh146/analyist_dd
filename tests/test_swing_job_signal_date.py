#!/usr/bin/env python3
"""run_swing_job._signal_date 회귀 — 시그널 기준일은 달력일(UTC)이 아니라 최근 거래일.

WHY(실측 2026-11-23 08:31 KST): job-runner 컨테이너의 TZ 는 UTC 라 `date.today()` 가
월요일 08:30 실행에서 **일요일(11-22)** 을 찍었다 → swing 피드 20건의 signal_date 가
비거래일로 발행됐다. 수리는 ① DB 의 max(trade_date) 사용 ② DB 실패 시 **KST 달력일**
폴백(UTC 로 되돌아가지 않는다).
"""
import importlib.util
import os
from datetime import datetime, timedelta

import psycopg2
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "services", "job-runner", "app", "scripts", "run_swing_job.py")


def _mod():
    spec = importlib.util.spec_from_file_location("run_swing_job_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_db_failure_falls_back_to_kst_not_utc(monkeypatch):
    m = _mod()

    def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(psycopg2, "connect", boom)
    out = m._signal_date()
    kst_today = (datetime.utcnow() + timedelta(hours=9)).date().isoformat()
    assert out == kst_today, f"UTC 달력일로 폴백하면 안 된다: {out} != {kst_today}"


def test_uses_db_trade_date(monkeypatch):
    m = _mod()

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            assert "max(trade_date)" in sql

        def fetchone(self):
            from datetime import date
            return (date(2026, 11, 20),)

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(psycopg2, "connect", lambda **kw: FakeConn())
    assert m._signal_date() == "2026-11-20"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
