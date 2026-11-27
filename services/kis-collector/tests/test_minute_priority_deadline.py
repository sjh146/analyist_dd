"""분봉 수집의 우선순위·마감 동작 고정 (2026-11-27).

야간 창(23:00~)은 473종목이면 6시간이라 07:35 분봉 가드와 겹친다. 그래서
  (1) 패널 종목을 먼저 채우고
  (2) 마감 시각을 넘기면 남은 종목을 건너뛰되 'skipped' 로 보고한다(조용한 절단 금지).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kis_app.collectors.minute_collector import MinuteCollector  # noqa: E402


class _Storage:
    def __init__(self):
        self.saved = []

    def save_minute_bars(self, code, rows):
        self.saved.append(code)
        return len(rows)


class _Collector(MinuteCollector):
    def __init__(self, codes_seen):
        self.codes_seen = codes_seen

    def collect_stock(self, stock_code, market, target_date, **kw):
        self.codes_seen.append(stock_code)
        return [{"time": "0900", "close": 1.0}]


def _run(monkeypatch_deadline=None, priority=None, universe=None):
    seen = []
    storage = _Storage()
    col = _Collector(seen)
    col._storage = storage
    summary = col.collect("20261127", universe=universe or [("111111", "KOSPI"), ("222222", "KOSPI"),
                                                            ("333333", "KOSDAQ")],
                          priority=priority, deadline=monkeypatch_deadline)
    return seen, summary


def test_priority_codes_are_collected_first():
    seen, summary = _run(priority=["333333"])
    assert seen[0] == "333333"
    assert summary["ok"] == 3


def test_deadline_skips_remaining_and_reports():
    seen, summary = _run(monkeypatch_deadline=datetime.now() - timedelta(seconds=1))
    assert seen == []
    assert summary["skipped"] == 3
    assert summary["total"] == 3


def test_no_deadline_collects_everything():
    seen, summary = _run(monkeypatch_deadline=datetime.now() + timedelta(hours=1))
    assert len(seen) == 3
    assert summary["skipped"] == 0
