"""researcher_cycle.status_after 의 '상시 감시'(recurring) 회귀 테스트.

WHY (실측 2026-09-29 20:0x): R21(수급 최신일 지연 상시 감시, recurring=true, KIS 호출 0회)은
감시 항목인데도 종전 규칙 `rc == 0 and passed and recurring → pending` 때문에
  · 한 번 통과하면         → pending (OK)
  · check 가 미달이면      → partial → 다음 틱 후보(pending)에서 빠진다
  · 러너가 크래시(rc=1/2)하면 → failed  → 역시 후보에서 빠진다
즉 **경보가 뜬 그 순간이 감시의 마지막 회차**가 됐다(가장 필요한 시점에 눈이 먼다).
감시 항목의 KPI 는 '한 번 통과'가 아니라 '계속 지켜봄'이다 → 어떤 결과에서도 pending.

경계를 고정한다:
  · recurring 은 rc·passed 와 무관하게 항상 pending (done/partial/failed 금지)
  · 조사형(investigate)은 종전대로 조사가 돌면 done — recurring 이어도 조사 완료는 종료다
  · 비(非)recurring 항목의 판정은 변하지 않는다(진짜 실패를 조용히 만들지 않는다)
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

RECUR = {"id": "R21", "kind": "collect", "recurring": True}
RECUR_INCR = {"id": "R22", "kind": "collect", "recurring": True, "incremental": True,
              "resumable_rc": [3]}
RECUR_INVEST = {"id": "R23", "kind": "investigate", "recurring": True}
PLAIN = {"id": "R1", "kind": "collect"}
INCR = {"id": "R3", "kind": "collect", "incremental": True, "resumable_rc": [3]}


def test_recurring_passing_stays_pending():
    assert rc.status_after(RECUR, 0, True) == "pending"


def test_recurring_failing_check_stays_pending():
    """경보(미달) 시에도 감시는 큐에 남아야 한다 — 종전엔 partial 로 사라졌다."""
    assert rc.status_after(RECUR, 0, False) == "pending"


def test_recurring_crash_rc_stays_pending():
    assert rc.status_after(RECUR, 1, None) == "pending"
    assert rc.status_after(RECUR, 2, None) == "pending"


def test_recurring_undeclared_resume_code_stays_pending():
    assert rc.status_after(RECUR_INCR, 3, True) == "pending"


def test_investigate_recurring_still_completes():
    assert rc.status_after(RECUR_INVEST, 0, True) == "done"
    assert rc.status_after(RECUR_INVEST, 1, None) == "failed"


def test_non_recurring_unchanged_golden():
    """비(非)recurring 판정 회귀 — 상시 감시 예외가 새면 여기서 깨진다."""
    assert rc.status_after(PLAIN, 0, True) == "done"
    assert rc.status_after(PLAIN, 0, False) == "partial"
    assert rc.status_after(INCR, 0, True) == "done"
    assert rc.status_after(INCR, 0, False) == "pending"
    assert rc.status_after(INCR, 3, False) == "pending"
    assert rc.status_after(INCR, 1, False) == "failed"
