#!/usr/bin/env python3
"""가드 거부가 '실행 중' 흔적(pidfile/state)을 남기지 않는지 검증 (2026-09-27 U3 실측 대응).

문제: `--start U3` 가 부하 가드로 거부(rc=3)됐는데 pidfile·state.json 에 pid 가 남았다 →
      다음 틱이 "사이클이 기록 없이 죽었다" 로 오보하고 원장에 가짜 실행실패를 남길 상태였다.
검증: ① execute() 가 거부로 끝나면 pidfile/state 를 지운다(내 pid 일 때만)
      ② 남의 pid 가 적힌 pidfile 은 지우지 않는다(뒤늦게 끝난 옛 자식이 락을 지우는 사고 방지)
"""
import json
import os
import shutil
import sys

sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
import model_engineer_cycle as m

BK = "/tmp/_cycle_refusal_bk"
os.makedirs(BK, exist_ok=True)
for p in (m.PIDFILE, m.STATE):
    if os.path.exists(p):
        shutil.copy2(p, os.path.join(BK, os.path.basename(p)))


def restore():
    for p in (m.PIDFILE, m.STATE):
        src = os.path.join(BK, os.path.basename(p))
        if os.path.exists(src):
            shutil.copy2(src, p)
        elif os.path.exists(p):
            os.remove(p)


ok = True
item = {"id": "TESTX", "title": "가드 거부 테스트", "command": "true",
        "metric": "wf_sweep_summary"}

try:
    # ── ① 내 pid 가 적힌 상태에서 거부 → 정리되어야 한다
    os.makedirs(m.RUNTIME, exist_ok=True)
    open(m.PIDFILE, "w").write(str(os.getpid()))
    json.dump({"id": "TESTX", "pid": os.getpid(), "started": m.now_kst().isoformat()},
              open(m.STATE, "w"))
    orig = m.guards
    m.guards = lambda force=False, it=None: (False, "테스트용 거부")
    rc = m.execute(dict(item), force=False)
    m.guards = orig
    a_ok = (rc == 3) and (not os.path.exists(m.PIDFILE)) and (not os.path.exists(m.STATE))
    print(f"[A] 거부 시 pidfile/state 정리(rc={rc}): {'PASS' if a_ok else 'FAIL'}")
    ok &= a_ok

    # ── ② 남의 pid 가 적힌 pidfile 은 보존
    open(m.PIDFILE, "w").write("999999")
    json.dump({"id": "OTHER", "pid": 999999, "started": m.now_kst().isoformat()},
              open(m.STATE, "w"))
    m.guards = lambda force=False, it=None: (False, "테스트용 거부")
    rc = m.execute(dict(item), force=False)
    m.guards = orig
    b_ok = os.path.exists(m.PIDFILE) and open(m.PIDFILE).read().strip() == "999999"
    print(f"[B] 남의 pid 는 보존: {'PASS' if b_ok else 'FAIL'}")
    ok &= b_ok
finally:
    restore()

print("\n전체:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
