"""되돌림(rollback) 감시 판정 회귀 테스트.

WHY: 자율 승격의 안전장치는 **되돌릴 수 있음**이다. 판정을 잘못하면 (a) 나쁜 챔피언을 계속 유지하거나
(b) 멀쩡한 챔피언을 흔들어 루프가 진동한다. 그래서 경계값을 잠근다: 경과 세션·증거 노화·
문턱(degrade_pct)·증거 부재.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import champion_rollback_monitor as rbm  # noqa: E402


def _obj(monitor=5, degrade=-0.2, max_age=14):
    return {"goal": {"rollback": {"monitor_sessions": monitor, "degrade_pct": degrade,
                                  "max_evidence_age_days": max_age}},
            "gates": {"auto_rollback": False}}


def _ev(pct, age_days=0, auc=0.5):
    return {"expectancy_pct": pct, "robust_auc": auc,
            "created_at": (dt.datetime.now() - dt.timedelta(days=age_days)).isoformat(timespec="seconds")}


def test_too_early_does_not_decide(monkeypatch):
    monkeypatch.setattr(rbm, "_evidence", lambda n: _ev(0.1 if n == "champion" else 0.5))
    monkeypatch.setattr(rbm, "last_swap", lambda path=None: {"ts": "2026-10-03T00:00:00", "prev_dir": "champion_prev_x"})
    monkeypatch.setattr(rbm, "trading_sessions_since", lambda ts, repo=None: 2)
    v = rbm.verdict(_obj())
    assert v["verdict"] == "too_early", v


def test_rollback_when_degraded_beyond_threshold(monkeypatch):
    monkeypatch.setattr(rbm, "_evidence", lambda n: _ev(-0.5 if n == "champion" else 0.1))
    monkeypatch.setattr(rbm, "last_swap", lambda path=None: {"ts": "2026-10-03T00:00:00", "prev_dir": "champion_prev_x"})
    monkeypatch.setattr(rbm, "trading_sessions_since", lambda ts, repo=None: 7)
    v = rbm.verdict(_obj())
    assert v["verdict"] == "rollback" and v["degrade"] == -0.6, v


def test_watch_when_slightly_worse(monkeypatch):
    monkeypatch.setattr(rbm, "_evidence", lambda n: _ev(0.05 if n == "champion" else 0.15))
    monkeypatch.setattr(rbm, "last_swap", lambda path=None: {"ts": "2026-10-03T00:00:00", "prev_dir": "champion_prev_x"})
    monkeypatch.setattr(rbm, "trading_sessions_since", lambda ts, repo=None: 7)
    v = rbm.verdict(_obj())
    assert v["verdict"] == "watch", v


def test_ok_when_improved(monkeypatch):
    monkeypatch.setattr(rbm, "_evidence", lambda n: _ev(0.40 if n == "champion" else 0.10))
    monkeypatch.setattr(rbm, "last_swap", lambda path=None: {"ts": "2026-10-03T00:00:00", "prev_dir": "champion_prev_x"})
    monkeypatch.setattr(rbm, "trading_sessions_since", lambda ts, repo=None: 7)
    v = rbm.verdict(_obj())
    assert v["verdict"] == "ok", v


def test_no_evidence_is_not_a_rollback(monkeypatch):
    """증거가 없으면 '판정 불가'다 — 근거 없이 챔피언을 흔들지 않는다."""
    monkeypatch.setattr(rbm, "_evidence", lambda n: None)
    monkeypatch.setattr(rbm, "last_swap", lambda path=None: {"ts": "2026-10-03T00:00:00", "prev_dir": "champion_prev_x"})
    v = rbm.verdict(_obj())
    assert v["verdict"] == "no_evidence", v


def test_stale_evidence_is_not_a_rollback(monkeypatch):
    monkeypatch.setattr(rbm, "_evidence", lambda n: _ev(-1.0, age_days=30))
    monkeypatch.setattr(rbm, "last_swap", lambda path=None: {"ts": "2026-09-01T00:00:00", "prev_dir": "champion_prev_x"})
    monkeypatch.setattr(rbm, "trading_sessions_since", lambda ts, repo=None: 30)
    v = rbm.verdict(_obj())
    assert v["verdict"] == "stale_evidence", v


def test_no_swap_history_is_silent(monkeypatch):
    monkeypatch.setattr(rbm, "last_swap", lambda path=None: None)
    v = rbm.verdict(_obj())
    assert v["verdict"] == "no_swap", v


def test_repo_objective_has_rollback_config():
    import objective
    obj = objective.load()
    rb = obj["goal"]["rollback"]
    assert rb["monitor_sessions"] >= 1 and rb["degrade_pct"] < 0
    # auto_rollback 은 '정책'이라 값이 바뀔 수 있다(2026-10-04 켬) — 타입·존재만 잠근다.
    assert isinstance(obj["gates"].get("auto_rollback"), bool)
