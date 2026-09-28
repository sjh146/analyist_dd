"""researcher_cycle.status_after 의 '재개 신호'(resumable_rc) 회귀 테스트.

WHY (실측 2026-09-28 20:00 틱): 수집 러너는 자기 호출 상한에 걸리면 **스스로 부분 완료**로 끝난다
— `scripts/kis_supply_extend_history.py` 는 hit_limit 시 `return 3`("실행당 호출 상한 도달 — 중단,
다음 실행에서 재개")이다. 그런데 사이클이 `rc != 0 → failed` 로 상태를 적으면 `next_item()` 이
**pending 만** 후보로 보므로 그 항목은 큐에서 영구히 빠진다. 실측: R3(수급 800종목 확장)가
343→378종목으로 진행 중이던 실행(신규 11,967행 적재·자기신고 일치)이 rc=3 이라는 이유로
`status=failed` 가 되어 이후 틱들이 R3 를 다시 집지 않았다 — 점진 수집이 조용히 정지했다.

경계를 두 방향으로 고정한다:
  · 항목이 `resumable_rc` 로 선언한 rc 는 pending(재개) — 점진 수집이 이어진다
  · 선언이 없거나 선언 밖의 rc(1/2 = 크래시·오류)는 failed — 진짜 실패를 부분 완료로 오독하지 않는다
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

COLLECT_INCR = {"id": "R3", "kind": "collect", "incremental": True, "resumable_rc": [3]}
COLLECT_PLAIN = {"id": "R1", "kind": "collect"}
INVESTIGATE = {"id": "R2", "kind": "investigate", "incremental": True, "resumable_rc": [3]}


def test_goal_met_is_done():
    assert rc.status_after(COLLECT_INCR, 0, True) == "done"


def test_below_target_with_rc0_stays_pending_incremental():
    assert rc.status_after(COLLECT_INCR, 0, False) == "pending"


def test_below_target_with_rc0_without_incremental_is_partial():
    assert rc.status_after(COLLECT_PLAIN, 0, False) == "partial"


def test_declared_resume_code_keeps_pending():
    # 실측 시나리오: 호출 상한 도달 → exit 3 → 재개 예약(과거에는 failed 가 되어 큐에서 빠졌다)
    assert rc.status_after(COLLECT_INCR, 3, False) == "pending"


def test_undeclared_nonzero_is_failed():
    # 선언이 없으면 종전대로 실패다 — 기본값을 넓혀 진짜 실패를 감추지 않는다.
    assert rc.status_after(COLLECT_PLAIN, 3, False) == "failed"


def test_crash_codes_are_failed_even_with_declaration():
    # 러너 크래시(1)·래퍼 인자오류(2)는 부분 완료가 아니다.
    assert rc.status_after(COLLECT_INCR, 1, False) == "failed"
    assert rc.status_after(COLLECT_INCR, 2, False) == "failed"


def test_crash_code_below_target_is_not_done():
    # 실패할 수 없는 상태 매핑 금지: rc != 0 인데 목표를 넘겼다고 done 으로 적지 않는다.
    assert rc.status_after(COLLECT_INCR, 3, True) == "pending"


def test_investigate_ignores_resume_code():
    assert rc.status_after(INVESTIGATE, 0, False) == "done"
    assert rc.status_after(INVESTIGATE, 3, False) == "failed"


def test_resumable_rc_accepts_scalar_and_missing():
    assert rc._resumable_rc({"resumable_rc": 3}) == (3,)
    assert rc._resumable_rc({}) == ()
    assert rc._resumable_rc({"resumable_rc": None}) == ()
    assert rc._resumable_rc({"resumable_rc": ["3", 5]}) == (3, 5)
