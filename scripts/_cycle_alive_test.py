#!/usr/bin/env python3
"""구동기 pid 생존 판정 회귀 테스트 (scripts/model_engineer_cycle.py:_pid_alive).

왜 필요한가(실측 사고 2026-09-25 22:00):
  대기형 런처가 **passwordless root 로** U1(--run) 을 띄웠다. 그러면 jhshi 사용자가
  `os.kill(pid, 0)` 을 할 때 **EPERM** 이 온다(프로세스는 살아서 패널 빌드 중).
  `_pid_alive` 가 OSError 전체를 '죽음'으로 처리하던 탓에
    ① state.json fallback 까지 무력화 → 틱이 살아있는 U1 을 "기록 없이 죽었다"로 오보고
    ② pidfile 이 사라진 상태라면 교차 락이 풀려 **두 번째 무거운 실험**이 동시에 시작될 수 있었다
       (4코어에서 학습 2개 = 과거 SIGKILL 사고의 재료).
  EPERM 은 '죽음'이 아니라 **'존재하지만 내 것이 아님'** 이라는 규칙을 회귀로 고정한다.

실행: python3 scripts/_cycle_alive_test.py   (root 불필요 · 수 초)
"""
import builtins
import io
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

REAL_OPEN = builtins.open
FAILS = []


def _alive(pid, kill_exc=None, cmdline=None, open_exc=None):
    """_pid_alive 를 가짜 os.kill / 가짜 /proc/<pid>/cmdline 로 호출."""
    def fake_kill(_p, _s):
        if kill_exc is not None:
            raise kill_exc

    def fake_open(path, mode="r", *a, **k):
        if str(path).endswith("/cmdline"):
            if open_exc is not None:
                raise open_exc
            return io.BytesIO(cmdline if cmdline is not None else b"")
        return REAL_OPEN(path, mode, *a, **k)

    with mock.patch("os.kill", side_effect=fake_kill), \
         mock.patch.object(builtins, "open", side_effect=fake_open):
        return m._pid_alive(pid)


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got={got} want={want}")
    if not ok:
        FAILS.append(name)


def main():
    cycle = b"/usr/bin/python3\x00/somewhere/model_engineer_cycle.py\x00--run\x00U1\x00"
    other = b"/usr/bin/python3\x00/usr/bin/vim\x00notes.txt\x00"

    print("① _pid_alive — EPERM(남의 프로세스) 회귀")
    # 핵심: EPERM 인데 cmdline 이 우리 사이클이면 '살아있음'
    check("EPERM + cycle cmdline", _alive(12345, PermissionError(1, "EPERM"), cycle), True)
    # EPERM 이고 cmdline 을 못 읽으면 보수적으로 '살아있음'(차단이 안전한 쪽)
    check("EPERM + cmdline EACCES",
          _alive(12345, PermissionError(1, "EPERM"), None, PermissionError(13, "EACCES")), True)
    # EPERM 이지만 cmdline 이 우리 스크립트가 아니면 pid 재사용 → '죽음'
    check("EPERM + 다른 프로세스(pid 재사용)",
          _alive(12345, PermissionError(1, "EPERM"), other), False)

    print("② _pid_alive — 진짜 죽음/정상 케이스")
    check("ESRCH(진짜 없음)", _alive(12345, ProcessLookupError(3, "ESRCH"), cycle), False)
    check("생존 + cycle cmdline", _alive(12345, None, cycle), True)
    check("생존 + 다른 프로세스", _alive(12345, None, other), False)
    check("기타 OSError", _alive(12345, OSError(22, "EINVAL"), cycle), False)

    print("③ 교차 락(peer_running)도 같은 함정을 피하는가")
    # 상대 역할 pidfile 에 root 소유(EPERM) 사이클 pid 가 적혀 있으면 '실행 중'이어야 한다.
    with mock.patch.object(m, "_pid_alive", return_value=True), \
         mock.patch.object(m, "PIDFILE", os.path.join(m.PROJ, "data/reports/me_cycle/running.pid")), \
         mock.patch.object(builtins, "open",
                           side_effect=lambda p, *a, **k: io.StringIO("4242")):
        pid, rel = m.peer_running()
    check("peer_running 이 살아있는 상대를 인식", (pid, rel), (4242, "data/reports/res_cycle/running.pid"))

    print("④ 실환경: 실행 중 사이클이 있으면 running_pid() 가 잡아야 한다")
    pid = m.running_pid()
    print(f"  INFO  running_pid() = {pid} (None 이어도 실패는 아니다 — 지금 안 돌 수 있다)")
    if pid:
        check("실행 중 pid 의 _pid_alive", m._pid_alive(pid), True)

    print()
    if FAILS:
        print(f"실패 {len(FAILS)}건: {FAILS}")
        return 1
    print("전부 통과 — EPERM 을 '죽음'으로 오판하지 않는다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
