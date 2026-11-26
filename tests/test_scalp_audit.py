"""scalp_audit 판정 경계 테스트 — 브로커 포지션 '판정 불가'를 '플랫'으로 오독하지 않는지.

WHY: 2026-11-26 10:47 브리지가 내려간 뒤 감사가 매 5분 journal_without_broker(069510)를
고위험으로 보고했다. 저널 보유 종목은 실제로 브로커에 있었고, 원인은 포지션 응답을 못 읽은
것(unknown)이었다. 판정 불가는 불일치가 아니므로 불일치 검사에서 제외돼야 한다.
"""
from __future__ import annotations

import sqlite3

from scripts import scalp_audit


def _journal():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, ts TEXT, code TEXT, screener TEXT,"
        " qty INTEGER, price REAL, stop_pct REAL, tp_pct REAL, hwm_price REAL,"
        " exit_price REAL, exit_ts TEXT)"
    )
    conn.execute(
        "INSERT INTO trades (ts, code, screener, qty, price) VALUES"
        " ('2099-01-01T09:00:00+09:00', '069510', 'daytrading', 1, 24203.0)"
    )
    conn.commit()
    return conn


def _responder(bridge_ok=True, positions=None, positions_raises=False):
    def fake_get(url, timeout=8.0):
        if url.endswith("/health"):
            if not bridge_ok:
                raise TimeoutError("timed out")
            return {"ok": True, "connected": True}
        if url.endswith("/positions"):
            if positions_raises:
                raise TimeoutError("timed out")
            return {"positions": list(positions or [])}
        if url.endswith("/orders"):
            return {"orders": []}
        if url.endswith("/daily_pnl"):
            return {"daily_pnl": {"items": []}}
        if "screener_latest.json" in url:
            return {"generated_at": "2026-11-26T13:05:44+09:00",
                    "candidates": {"daytrading": {"items": []}}}
        raise AssertionError(url)
    return fake_get


def _kinds(state):
    return [f["kind"] for f in state["findings"]]


def test_bridge_down_does_not_claim_a_journal_broker_mismatch(monkeypatch):
    monkeypatch.setattr(scalp_audit, "_get", _responder(bridge_ok=False))
    monkeypatch.setattr(scalp_audit, "_journal_copy", _journal)
    state = scalp_audit.audit()
    assert "bridge_down" in _kinds(state)
    assert "journal_without_broker" not in _kinds(state)
    assert "broker_without_journal" not in _kinds(state)
    assert state["positions_known"] is False
    assert state["reconcile"].startswith("unverified")


def test_positions_read_failure_is_not_a_mismatch(monkeypatch):
    monkeypatch.setattr(scalp_audit, "_get", _responder(positions_raises=True))
    monkeypatch.setattr(scalp_audit, "_journal_copy", _journal)
    state = scalp_audit.audit()
    assert "positions_unreadable" in _kinds(state)
    assert "journal_without_broker" not in _kinds(state)
    assert state["positions_known"] is False


def test_known_positions_still_report_real_mismatches(monkeypatch):
    monkeypatch.setattr(scalp_audit, "_get",
                        _responder(positions=[{"code": "005930", "qty": 3}]))
    monkeypatch.setattr(scalp_audit, "_journal_copy", _journal)
    state = scalp_audit.audit()
    assert "journal_without_broker" in _kinds(state)
    assert "broker_without_journal" in _kinds(state)
    assert state["positions_known"] is True
    assert state["reconcile"] == "checked"
