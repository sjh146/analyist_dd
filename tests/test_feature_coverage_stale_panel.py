"""feature_coverage_report 가 낡은 패널로 '오늘 측정'을 위조하지 않는지.

실측 2026-11-23: exp_panel 캐시 meta.last_trade_date = 2026-10-06 인데, 리포트를 돌리면
그 값이 computed_at=now() 로 upsert 돼 dq_feature_oldest_age_days 가 0 으로 리셋된다.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import feature_coverage_report as fcr  # noqa: E402


def test_staleness_days():
    today = dt.date(2026, 11, 23)
    assert fcr.panel_staleness("2026-10-06", today=today) == 48
    assert fcr.panel_staleness("2026-11-20", today=today) == 3


def test_staleness_unknown_is_none():
    assert fcr.panel_staleness(None) is None
    assert fcr.panel_staleness("not-a-date") is None


def test_stale_guard_threshold_is_below_observed_gap():
    """기본 문턱은 주말 하나(3일)는 넘기고 6주(48일)는 막는 범위여야 한다."""
    assert 3 <= fcr.PANEL_STALE_DAYS <= 15
