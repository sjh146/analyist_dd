#!/usr/bin/env python3
"""회귀 테스트: system_hygiene.zombie_decision — 호스트 좀비 **증가분** 판정.

배경(실측 2026-10-09): stock_xgboost_ml 컨테이너 PID 1 이 subprocess(timeout/python)를 reap 하지
않아 좀비가 9 → 17 로 계단 상승했다. 좀비는 컨테이너를 재시작하기 전까지 **줄지 않으므로** 누적값에
breach 문턱(10)을 걸면 한 번 넘어간 뒤 영원히 매 점검 '위반'으로 남는다(값이 변하지 않는 메트릭 +
문턱 = 상시 오탐 — `flapping_delta`/RestartCount 와 같은 함정). 판정은 증가분만 본다.

계약:
 - 직전 관측이 없으면(첫 관측) breach 로 올리지 않는다(증가의 증거가 없다)
 - 누적 불변(정체) → breach 아님  ← 이 테스트가 고친 버그의 회귀 방지선
 - 이미 큰데 증가 → breach
 - 문턱 미만(누적) → warn(정보)
 - 컨테이너 재시작으로 감소 → breach 아님
"""
import importlib.util
import os

import pytest

HYGIENE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "system_hygiene.py")


def _load():
    spec = importlib.util.spec_from_file_location("system_hygiene_zomb_under_test", HYGIENE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def hygiene():
    return _load()


def test_stable_large_count_is_not_breach(hygiene):
    """핵심 회귀: 17 이 그대로여도 breach 가 아니다(컨테이너 재시작 전까지 안 줄어든다)."""
    level, delta = hygiene.zombie_decision(17, 17)
    assert level == "warn"
    assert delta == 0


def test_jump_beyond_threshold_is_breach(hygiene):
    """9 → 17 계단 상승(실측)은 breach 로 잡혀야 한다."""
    level, delta = hygiene.zombie_decision(17, 9)
    assert level == "breach"
    assert delta == 8


def test_first_observation_is_not_breach(hygiene):
    """직전 관측이 없으면 증가의 증거가 없다 → breach 로 올리지 않는다."""
    level, delta = hygiene.zombie_decision(17, None)
    assert level == "warn"
    assert delta is None


def test_decrease_after_restart_is_not_breach(hygiene):
    """컨테이너 재시작으로 좀비가 씻겨 감소 → breach 아님."""
    level, delta = hygiene.zombie_decision(3, 17)
    assert level == "warn"
    assert delta == -14


def test_no_zombies_is_quiet(hygiene):
    level, delta = hygiene.zombie_decision(0, 0)
    assert level is None
    assert delta == 0


def test_below_breach_growth_is_warn(hygiene):
    """문턱 미만에서의 증가는 정보(warn)일 뿐 breach 가 아니다."""
    level, delta = hygiene.zombie_decision(5, 3)
    assert level == "warn"
    assert delta == 2


def test_crossing_threshold_with_growth_is_breach(hygiene):
    level, delta = hygiene.zombie_decision(10, 9)
    assert level == "breach"
    assert delta == 1


def test_zero_after_cleared_is_quiet(hygiene):
    level, delta = hygiene.zombie_decision(0, 5)
    assert level is None
    assert delta == -5
