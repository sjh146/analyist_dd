"""feature_coverage_report 가 낡은 패널로 '오늘 측정'을 위조하지 않는지.

실측 2026-11-23: exp_panel 캐시 meta.last_trade_date = 2026-10-06 인데, 리포트를 돌리면
그 값이 computed_at=now() 로 upsert 돼 dq_feature_oldest_age_days 가 0 으로 리셋된다.

실측 2026-11-24 (R49 rc=1): 호스트에서 `--ignore-cache` 재측정이 두 겹으로 막혔다.
  (1) sys.path 후보가 "/app"·cwd 뿐 → `ModuleNotFoundError: No module named 'app'`
  (2) 방금 새로 빌드한 신선 패널에도 '캐시 낡음' 가드가 걸려 exit 3 (쓰기 거부)
"""
import datetime as dt
import os
import sys

import pandas as pd

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


def test_app_roots_include_host_service_path():
    """호스트(repo 루트)에서 돌 때도 `app` 패키지를 찾을 수 있어야 한다."""
    roots = fcr._app_roots()
    assert os.path.join(fcr._REPO_ROOT, "services", "xgboost-ml") in roots


class _DummyPG:
    def close(self):
        self.closed = True


def _panel():
    return pd.DataFrame({
        "stock_code": ["A", "A", "B", "B"],
        "date": ["2026-11-19", "2026-11-20", "2026-11-19", "2026-11-20"],
        "feat_a": [0.0, 1.0, 2.0, 3.0],
        "feat_b": [0.0, 0.0, 0.0, 0.0],
    })


def _run_main(monkeypatch, argv):
    written = {}
    monkeypatch.setattr(fcr, "_pg_connect", lambda: _DummyPG())
    monkeypatch.setattr(fcr, "upsert_coverage", lambda pg, rows: written.setdefault("rows", rows))
    monkeypatch.setattr(fcr, "_build_small_panel", lambda pg, n_stocks, days: (_panel(), days))
    monkeypatch.setattr(fcr, "_load_panel_cached", lambda: (_panel(), 120))
    monkeypatch.setattr(fcr, "_panel_last_trade_date", lambda: "2026-10-06")
    monkeypatch.setattr(sys, "argv", ["feature_coverage_report.py"] + argv)
    return fcr.main(), written


def test_fresh_build_is_not_blocked_by_stale_cache(monkeypatch):
    """신선하게 새로 빌드한 패널은 낡은 캐시 meta 때문에 쓰기를 거부당하면 안 된다."""
    rc, written = _run_main(monkeypatch, ["--ignore-cache", "--stocks", "2", "--days", "5"])
    assert rc == 0
    assert written.get("rows"), "신선 빌드인데 upsert 가 호출되지 않았다"


def test_cache_path_still_refuses_stale_panel(monkeypatch):
    """캐시를 재사용하는 경로에서는 낡음 가드가 그대로 살아 있어야 한다."""
    rc, written = _run_main(monkeypatch, [])
    assert rc == 3
    assert not written, "낡은 캐시인데 기록됐다(신선도 위조)"


def test_skip_when_already_fresh(monkeypatch):
    """이미 신선한 측정이 있으면 25분짜리 재빌드를 반복하지 않는다."""
    built = {"n": 0}
    written = {}
    monkeypatch.setattr(fcr, "_pg_connect", lambda: _DummyPG())
    monkeypatch.setattr(fcr, "recent_measurement", lambda pg, wd, hours: (196, 196))

    def _boom(pg, n_stocks, days):
        built["n"] += 1
        return _panel(), days

    monkeypatch.setattr(fcr, "_build_small_panel", _boom)
    monkeypatch.setattr(fcr, "upsert_coverage", lambda pg, rows: written.setdefault("rows", rows))
    monkeypatch.setattr(sys, "argv", ["feature_coverage_report.py", "--ignore-cache",
                                      "--days", "120", "--skip-if-fresh-hours", "20"])
    assert fcr.main() == 0
    assert built["n"] == 0 and not written


def test_stale_measurement_does_not_skip(monkeypatch):
    """신선한 측정이 없으면(부분 신선 포함) 정상적으로 재빌드한다."""
    built = {"n": 0}
    monkeypatch.setattr(fcr, "_pg_connect", lambda: _DummyPG())
    monkeypatch.setattr(fcr, "recent_measurement", lambda pg, wd, hours: (5, 196))
    monkeypatch.setattr(fcr, "upsert_coverage", lambda pg, rows: None)

    def _build(pg, n_stocks, days):
        built["n"] += 1
        return _panel(), days

    monkeypatch.setattr(fcr, "_build_small_panel", _build)
    monkeypatch.setattr(sys, "argv", ["feature_coverage_report.py", "--ignore-cache",
                                      "--days", "120", "--skip-if-fresh-hours", "20"])
    assert fcr.main() == 0
    assert built["n"] == 1
