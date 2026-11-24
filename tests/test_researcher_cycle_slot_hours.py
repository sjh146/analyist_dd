"""시각창(slot_hours) 프로브가 회전 양보에 굶지 않는지 고정한다.

WHY (실측 2026-11-24): R48 은 "직전 거래일 봉이 07:30 이전에 발행되는가"를 개장 전 틱에서만
판정할 수 있는데, pick_item 의 회전 양보(더 오래 굶은 후보 우선)가 매번 다른 pending 을
집었다 — 원장 04:48 R27 / 06:47 R41, R48 은 실행 0건으로 영구 pending 이었다. 시각창을
놓치면 증거가 사라지는 항목은 양보에서 제외한다.
"""
import importlib.util
import pathlib
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def _item(iid, prio, recurring=False, command="true", status="pending", slots=None):
    it = {"id": iid, "title": iid, "priority": prio, "status": status,
          "recurring": recurring, "command": command}
    if slots is not None:
        it["slot_hours"] = slots
    return it


def _backlog(*items):
    return {"schema": 1, "items": list(items)}


def _at(hour):
    return datetime(2026, 11, 24, hour, 47)


def test_slot_probe_wins_over_more_starved_pending(monkeypatch):
    b = _backlog(_item("R48", 4, recurring=True, slots=[0, 2, 4, 6]),
                 _item("R41", 7))
    monkeypatch.setattr(rc, "now_kst", lambda: _at(6))
    monkeypatch.setattr(rc, "_recent_recurring_ids", lambda *a, **k: set())
    monkeypatch.setattr(rc, "_last_run_map",
                        lambda: {"R48": datetime(2026, 11, 24, 2, 0),
                                 "R41": datetime(2026, 11, 1)})
    assert rc.pick_item(b)["id"] == "R48"


def test_slot_probe_yields_outside_its_window(monkeypatch):
    b = _backlog(_item("R48", 4, recurring=True, slots=[0, 2, 4, 6]),
                 _item("R41", 7))
    monkeypatch.setattr(rc, "now_kst", lambda: _at(16))
    monkeypatch.setattr(rc, "_recent_recurring_ids", lambda *a, **k: set())
    monkeypatch.setattr(rc, "_last_run_map",
                        lambda: {"R48": datetime(2026, 11, 24, 2, 0),
                                 "R41": datetime(2026, 11, 1)})
    assert rc.pick_item(b)["id"] == "R41"


def test_slot_probe_still_respects_cooldown(monkeypatch):
    b = _backlog(_item("R48", 4, recurring=True, slots=[4]),
                 _item("R41", 7))
    monkeypatch.setattr(rc, "now_kst", lambda: _at(4))
    monkeypatch.setattr(rc, "_recent_recurring_ids", lambda *a, **k: {"R48"})
    monkeypatch.setattr(rc, "_last_run_map",
                        lambda: {"R48": datetime(2026, 11, 24, 2, 0),
                                 "R41": datetime(2026, 11, 1)})
    assert rc.pick_item(b)["id"] == "R41"


def test_no_slot_key_keeps_legacy_rotation(monkeypatch):
    b = _backlog(_item("R48", 4, recurring=True), _item("R41", 7))
    monkeypatch.setattr(rc, "now_kst", lambda: _at(4))
    monkeypatch.setattr(rc, "_recent_recurring_ids", lambda *a, **k: set())
    monkeypatch.setattr(rc, "_last_run_map",
                        lambda: {"R48": datetime(2026, 11, 24, 2, 0),
                                 "R41": datetime(2026, 11, 1)})
    assert rc.pick_item(b)["id"] == "R41"


def test_slot_hour_ignores_malformed_values():
    assert rc._slot_hour({"slot_hours": ["4", 6]}, 4) is True
    assert rc._slot_hour({"slot_hours": "4"}, 4) in (True, False)
    assert rc._slot_hour({"slot_hours": ["x"]}, 4) is False
    assert rc._slot_hour({}, 4) is False
