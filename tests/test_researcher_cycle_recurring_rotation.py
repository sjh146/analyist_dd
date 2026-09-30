"""researcher_cycle.pick_item 의 '상시 감시 굶주림 방지'(recurring cooldown) 회귀 테스트.

WHY (실측 2026-09-30 20:0x): recurring 항목은 status_after() 가 어떤 결과에서도 pending 을
유지한다(tests/test_researcher_cycle_recurring.py — 경보가 뜬 순간 감시가 죽지 않게 하는
의도된 설계). 그런데 구동기는 (priority, id) 정렬만 하므로 **앞 번호의 상시 감시가 매 틱
1순위를 독식**한다. 실측: R21(prio 6, recurring, KIS 0회) vs R23(prio 7) — 원장에 R21 실행
0건, R23 영구 pending(둘 다 큐에 있으나 R21 만 뽑힌다). 앞서는 R3·R12(prio 3·4)가 있어
가려졌을 뿐이고, 그들이 done 이 되는 순간 드러난다.

경계를 고정한다:
  · 최근 실행된 recurring 은 이번 틱 후보에서 밀린다 → 뒤 번호의 pending 이 먼저 집힌다
  · 큐에 다른 실행 가능 항목이 없으면 감시를 다시 집는다(회전이 감시를 죽이지 않는다)
  · 비(非)recurring 항목은 쿨다운 대상이 아니다
  · 범위 밖(오래 전) 실행 기록은 쿨다운에 걸리지 않는다
  · force 는 쿨다운을 무시한다(운영자 명시 실행)
  · 원장이 깨져도 예외를 올리지 않는다(감시·수집을 죽이지 않는다)
"""
import importlib.util
import pathlib
from datetime import timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def _item(iid, prio, recurring=False, command="true", status="pending"):
    return {"id": iid, "title": iid, "priority": prio, "status": status,
            "recurring": recurring, "command": command}


def _backlog(*items):
    return {"schema": 1, "items": list(items)}


def test_recent_recurring_is_yielded_to_later_pending(monkeypatch):
    b = _backlog(_item("R21", 6, recurring=True), _item("R23", 7))
    monkeypatch.setattr(rc, "_recent_recurring_ids", lambda *a, **k: {"R21"})
    assert rc.pick_item(b)["id"] == "R23"


def test_recurring_reruns_when_nothing_else_is_runnable(monkeypatch):
    """양보한 결과 남는 후보가 없으면 감시를 다시 집어야 한다(감시 공백 금지)."""
    b = _backlog(_item("R21", 6, recurring=True),
                 _item("R99", 7, command=None))  # command 없음 → 실행 불가
    monkeypatch.setattr(rc, "_recent_recurring_ids", lambda *a, **k: {"R21"})
    assert rc.pick_item(b)["id"] == "R21"


def test_cooldown_applies_only_to_recurring(monkeypatch):
    """비(非)recurring 항목은 최근 실행됐어도 밀리지 않는다."""
    b = _backlog(_item("R10", 4), _item("R21", 6, recurring=True))
    monkeypatch.setattr(rc, "_recent_recurring_ids", lambda *a, **k: {"R10", "R21"})
    assert rc.pick_item(b)["id"] == "R10"


def test_force_ignores_cooldown(monkeypatch):
    b = _backlog(_item("R21", 6, recurring=True), _item("R23", 7))

    def _boom(*a, **k):
        raise AssertionError("force 인데 쿨다운을 조회했다")

    monkeypatch.setattr(rc, "_recent_recurring_ids", _boom)
    assert rc.pick_item(b, force=True)["id"] == "R21"


def test_recent_ids_filters_by_window(monkeypatch):
    now = rc.now_kst()
    older = rc.RECUR_COOLDOWN_MIN + 30
    rows = [
        {"id": "R21", "ts": (now - timedelta(minutes=5)).isoformat()},
        {"id": "R9", "ts": (now - timedelta(minutes=older)).isoformat()},
        {"id": "R23", "ts": "쓰레기"},           # 파싱 불가 → 무시
        {"ts": now.isoformat()},               # id 없음 → 무시
    ]
    monkeypatch.setattr(rc.base, "load_ledger", lambda *a, **k: rows)
    assert rc._recent_recurring_ids() == {"R21"}


def test_recent_ids_survives_broken_ledger(monkeypatch):
    def _boom(*a, **k):
        raise OSError("ledger unreadable")

    monkeypatch.setattr(rc.base, "load_ledger", _boom)
    assert rc._recent_recurring_ids() == set()


def test_no_recent_run_falls_through_to_base_order(monkeypatch):
    """쿨다운 대상이 없으면 종전 순서(priority, id) 그대로 — 회귀 없음."""
    b = _backlog(_item("R21", 6, recurring=True), _item("R23", 7))
    monkeypatch.setattr(rc, "_recent_recurring_ids", lambda *a, **k: set())
    assert rc.pick_item(b)["id"] == "R21"


def test_cooldown_covers_more_than_one_tick():
    """쿨다운은 틱 간격(120분)보다 충분히 길어야 한다.

    WHY(실측 2026-09-30 22:0x): 180분(=1.5틱)이면 '결과 보고 틱이 시작 기회를 삼키는' 구조나
    '부하 가드로 시작이 막히는' 구조에서 다음 시작 틱에 쿨다운이 이미 만료돼 양보가 발동하지
    않는다(R21 이 다시 뽑혀 R23 이 영구 pending). 최소 2틱(240분)을 경계로 고정한다.
    """
    assert rc.RECUR_COOLDOWN_MIN >= 240, (
        "쿨다운이 2틱 미만이면 막힌 틱 하나에 양보가 무효화된다")
