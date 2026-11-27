#!/usr/bin/env python3
"""구동기 '고아 스윕' 감지 회귀 테스트 — sweep_pid_from_cmdlines.

배경(2026-11-27 실측): docker exec 클라이언트가 죽어도 컨테이너 안 python 은 살아남아
wf_label_sweep 3개가 동시에 돌았고(4코어에 3.7코어 점유, load1 14.5), 그중 2개가 **같은
--out/--summary-out** 에 동시에 쓰고 있었다. 구동기는 pidfile 만 보므로 '실행 중: 없음' 이라
보고했다. 이 테스트는 그 감지가 (a) 실제 스윕은 잡고 (b) 래퍼 셸·grep 은 잡지 않는지 고정한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as mec  # noqa: E402


def run():
    ok = 0
    fail = []

    def check(name, cond):
        nonlocal ok
        if cond:
            ok += 1
            print(f"  PASS {name}")
        else:
            fail.append(name)
            print(f"  FAIL {name}")

    cmds = [(101, "python\0-u\0scripts/wf_label_sweep.py\0--folds\0005\0--seeds\05\0")]
    got = mec.sweep_pid_from_cmdlines(cmds)
    check("python + wf_label_sweep 검출", got == [(101, "wf_label_sweep.py")])

    cmds = [(102, "python3\0-u\0scripts/wf_wave.py\0"),
            (103, "/usr/local/bin/python3.12\0scripts/train_curated.py\0")]
    check("wf_wave·train_curated 검출",
          [p for p, _ in mec.sweep_pid_from_cmdlines(cmds)] == [102, 103])

    cmds = [(104, "sh\0-c\0cd /app && python -u scripts/wf_label_sweep.py --seeds 3\0"),
            (105, "bash\0-c\0grep wf_label_sweep.py scripts/model_engineer_cycle.py\0")]
    check("셸 래퍼·grep 오탐 없음", mec.sweep_pid_from_cmdlines(cmds) == [])

    cmds = [(106, "python\0-u\0scripts/wf_label_sweep.py\0")]
    check("self_pids 제외", mec.sweep_pid_from_cmdlines(cmds, self_pids={106}) == [])

    cmds = [(107, "python\0scripts/wf_wave.py\0scripts/wf_wave.py\0")]
    check("중복 토큰 1건", mec.sweep_pid_from_cmdlines(cmds) == [(107, "wf_wave.py")])

    print(f"\n{ok}/{ok + len(fail)} PASS" + (f" — 실패: {', '.join(fail)}" if fail else ""))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(run())
