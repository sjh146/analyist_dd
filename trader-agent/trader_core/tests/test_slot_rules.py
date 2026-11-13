"""경로별 슬롯 규칙 테스트 (스윙 3 / 단타 2 — 전역 cap 은 호출부가 주입, 순수 함수 고정)."""
from trader_core.slot_rules import can_enter, reason, slot_cap


def test_swing_slot_is_three():
    assert slot_cap("swing", 3) == 3
    assert slot_cap("daytrading", 3) == 2
    assert slot_cap("close", 3) == 3          # 슬롯 미정의 경로는 전역 cap
    assert slot_cap(None, 3) == 3


def test_swing_full_blocks_swing_but_not_daytrading():
    open_by = {"swing": 3, "daytrading": 0}
    assert can_enter("swing", open_by, global_open=3, global_cap=3) is False
    # 스윙이 슬롯을 다 채워도 전역 cap(3)에 걸려 단타는 못 들어간다 = 총 노출 보호
    assert can_enter("daytrading", open_by, global_open=3, global_cap=3) is False
    assert "전역" in reason("daytrading", open_by, 3, 3)


def test_daytrading_slot_usable_when_global_room_exists():
    open_by = {"swing": 1, "daytrading": 1}
    assert can_enter("daytrading", open_by, global_open=2, global_cap=3) is True   # 1<2 슬롯, 2<3 전역
    open_by = {"swing": 1, "daytrading": 2}
    assert can_enter("daytrading", open_by, global_open=3, global_cap=3) is False  # 슬롯 포화
    assert "슬롯 포화" in reason("daytrading", open_by, 3, 3)


def test_planned_entries_count_against_slot():
    open_by = {"swing": 0, "daytrading": 1}
    assert can_enter("daytrading", open_by, 1, 3, planned_same_screener=1) is False


def test_overrides_are_honored():
    assert slot_cap("daytrading", 3, {"daytrading": 5}) == 5
