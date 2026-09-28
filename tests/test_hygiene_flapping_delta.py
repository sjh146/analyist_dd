#!/usr/bin/env python3
"""회귀 테스트: system_hygiene.flapping_delta — 재시작 **증가분** 판정.

배경(실측 2026-09-28): WSL 재부팅 연쇄 장애로 stock_krx_collector 의 docker RestartCount 가
23 이 됐다. 컨테이너는 이후 18시간 정상 가동 중이었는데도 누적 23 >= 문턱 5 로 **매 점검**
'재시작 반복' 경고가 떴다 — RestartCount 는 줄어들지 않으므로 누적값에 문턱을 걸면 영구
경고가 된다(값이 변하지 않는 메트릭 + 문턱 = 상시 오탐). 판정은 증가분만 본다.

계약:
 - 직전 관측이 없는 컨테이너(첫 관측·신규) → 경고 없음(최근 재시작의 증거가 없다)
 - 누적 불변 → 경고 없음  ← 이 테스트가 고친 버그의 회귀 방지선
 - 누적 증가 → 경고(증가분·누적 표기)
 - 컨테이너 재생성으로 누적이 리셋(감소) → 경고 없음
 - 진짜 크래시루프(점검마다 +N)는 계속 잡혀야 한다
"""
import importlib.util
import os

import pytest

HYGIENE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "system_hygiene.py")


def _load():
    spec = importlib.util.spec_from_file_location("system_hygiene_flap_under_test", HYGIENE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def hygiene():
    return _load()


def test_unchanged_cumulative_is_not_flapping(hygiene):
    """핵심 회귀: 누적 23 이 그대로여도 경고하지 않는다(18시간 정상 가동 중이었다)."""
    assert hygiene.flapping_delta({"stock_krx_collector": 23}, {"stock_krx_collector": 23}) == []


def test_first_observation_is_silent(hygiene):
    """직전 관측이 없으면 과거 흉터만 보고 경고하지 않는다(업그레이드 직후 첫 점검)."""
    assert hygiene.flapping_delta({"stock_krx_collector": 23}, {}) == []


def test_new_container_is_silent(hygiene):
    assert hygiene.flapping_delta({"newapi": 1, "old": 3}, {"old": 3}) == []


def test_increase_is_reported_with_delta_and_cumulative(hygiene):
    got = hygiene.flapping_delta({"krx": 25}, {"krx": 23})
    assert got == ["krx:+2회 (누적 25)"]


def test_reset_decrease_is_silent(hygiene):
    """컨테이너 재생성으로 카운트가 리셋되면 재시작이 아니라 교체다 — 경고 아님."""
    assert hygiene.flapping_delta({"krx": 0}, {"krx": 23}) == []


def test_crash_loop_is_still_caught_every_check(hygiene):
    """진짜 크래시루프(점검마다 재시작)는 침묵하면 안 된다."""
    assert hygiene.flapping_delta({"bad": 4}, {"bad": 2}) == ["bad:+2회 (누적 4)"]
    assert hygiene.flapping_delta({"bad": 6}, {"bad": 4}) == ["bad:+2회 (누적 6)"]


def test_multiple_containers_sorted(hygiene):
    got = hygiene.flapping_delta({"b": 2, "a": 5, "c": 1}, {"a": 4, "b": 2, "c": 1})
    assert got == ["a:+1회 (누적 5)"]


def test_prev_restart_counts_tolerates_missing_or_broken_file(hygiene, tmp_path, monkeypatch):
    monkeypatch.setattr(hygiene, "OUTDIR", str(tmp_path))
    assert hygiene._prev_restart_counts() == {}
    (tmp_path / "latest.json").write_text("{not json", encoding="utf-8")
    assert hygiene._prev_restart_counts() == {}
    (tmp_path / "latest.json").write_text('{"docker": {"restart_counts": {"krx": 7}}}',
                                          encoding="utf-8")
    assert hygiene._prev_restart_counts() == {"krx": 7}
