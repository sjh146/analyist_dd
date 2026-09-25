#!/usr/bin/env python3
"""회귀 테스트: system_hygiene.check_orphan_pidfiles 의 uid/EPERM 처리.

배경(실측 2026-09-25~26): 사이클이 root 로 시작되면 같은 pidfile 을 읽는 위생 점검이
`os.kill(pid, 0)` 에서 PermissionError 를 받는다. 이를 "죽었다"로 뭉뚱그리면 살아있는
사이클에 대해 매 틱 가짜 `breach(죽은 프로세스의 사이클 pidfile 잔존)` 가 뜬다
(7회 연속 오탐: 21:30·21:57·22:30·23:00·23:30·00:00·00:01).

계약: 죽음 판정은 ProcessLookupError(ESRCH) 만. PermissionError(EPERM) 는 살아있는 것으로,
그 밖의 OSError 는 /proc/<pid> 존재로 교차 확인. 죽은 pid 는 반드시 잡아야 한다(고친 뒤에도).
"""
import importlib.util
import os

import pytest

HYGIENE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "system_hygiene.py")


def _load():
    spec = importlib.util.spec_from_file_location("system_hygiene_under_test", HYGIENE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def hygiene(tmp_path, monkeypatch):
    mod = _load()
    pf_dir = tmp_path / "data" / "reports" / "me_cycle"
    pf_dir.mkdir(parents=True)
    monkeypatch.setattr(mod, "PROJ", str(tmp_path))
    mod._pidfile = pf_dir / "running.pid"  # 편의 핸들
    return mod


def test_dead_pid_is_detected(hygiene):
    """죽은 pid 가 적힌 pidfile 은 잔존으로 보고돼야 한다(오탐 수정이 탐지를 죽이지 않았는가)."""
    maxpid = max(int(d) for d in os.listdir("/proc") if d.isdigit())
    hygiene._pidfile.write_text(str(maxpid + 50000))
    assert hygiene.check_orphan_pidfiles() == ["engineer"]


def test_live_foreign_uid_pid_is_not_orphan(hygiene):
    """살아있는 타 uid 프로세스(통상 pid 1 = root)는 EPERM 을 낸다 — 죽음으로 오독 금지."""
    hygiene._pidfile.write_text("1")
    assert hygiene.check_orphan_pidfiles() == []


def test_live_own_pid_is_not_orphan(hygiene):
    hygiene._pidfile.write_text(str(os.getpid()))
    assert hygiene.check_orphan_pidfiles() == []


def test_missing_pidfile_is_not_reported(hygiene):
    hygiene._pidfile.unlink(missing_ok=True)
    assert hygiene.check_orphan_pidfiles() == []


def test_garbage_pidfile_is_reported(hygiene):
    hygiene._pidfile.write_text("not-a-pid")
    assert hygiene.check_orphan_pidfiles() == ["engineer"]
