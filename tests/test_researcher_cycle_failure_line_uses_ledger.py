"""틱 보고의 '✗ 실패' 판정은 원장 최신 회차를 따른다 (회귀 2026-11-25).

WHY(실측 2026-11-25 04:48 R48): 상시 감시 R48 의 러너는 00:47 실패(rc=1) 뒤 수리돼 04:48 재실행에서
rc=0 으로 통과했고 **원장에는 rc=0 회차가 마지막으로 남았는데**, 백로그 `result` 는 00:47 rc=1 에
멈춰 있었다(손편집·경합으로 낡음). 종전 조건은 `result.rc` 만 봤으므로 틱마다 '✗ 실패 — 로그 확인 필요'
를 찍어, **고쳐진 감시가 죽은 것처럼** 보고됐다. 반대 방향도 위험하다: 최신 회차가 진짜 실패면
조용해지면 안 되므로 원장 rc≠0 은 그대로 크게 찍는다.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

FAILED_REC = {"rc": 1, "log": "data/reports/res_cycle/logs/res_R48_old.log"}


def test_latest_rc_uses_last_record_per_id():
    led = [{"id": "R48", "rc": 1}, {"id": "R21", "rc": 0}, {"id": "R48", "rc": 0}]
    assert rc.latest_rc_by_id(led) == {"R48": 0, "R21": 0}


def test_latest_rc_tolerates_garbage():
    assert rc.latest_rc_by_id(None) == {}
    assert rc.latest_rc_by_id([None, "x", {"rc": 1}]) == {}


def test_stale_backlog_failure_does_not_flag_when_ledger_is_clean():
    item = {"id": "R48", "status": "pending", "recurring": True, "result": FAILED_REC}
    assert rc.item_should_flag_failure(item, {"R48": 0}) is False


def test_latest_ledger_failure_is_still_flagged():
    item = {"id": "R48", "status": "pending", "recurring": True,
            "result": {"rc": 0, "log": "x"}}
    assert rc.item_should_flag_failure(item, {"R48": 1}) is True


def test_falls_back_to_backlog_when_no_ledger_record():
    item = {"id": "R99", "status": "pending", "recurring": True, "result": FAILED_REC}
    assert rc.item_should_flag_failure(item, {}) is True


def test_non_recurring_pending_is_not_flagged():
    item = {"id": "R99", "status": "pending", "result": FAILED_REC}
    assert rc.item_should_flag_failure(item, {"R99": 1}) is False


def test_failed_status_always_flagged():
    item = {"id": "R99", "status": "failed", "result": {"rc": 0}}
    assert rc.item_should_flag_failure(item, {"R99": 0}) is True
