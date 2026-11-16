#!/usr/bin/env python3
"""중복 기동 차단(_duplicate_running) 검증 — 2026-11-17 CG306 이중 기동 사고의 회귀 테스트.

사고: `--start CG306 --force` 가 23:07:41·23:08:17 두 번 성공해 학습 2개가 동시에 돌았다.
      --force 는 가드 무시일 뿐 '같은 실험 두 번'의 허가가 아니다.

검증:
  [1] 같은 ID + 살아있는 pid        -> 차단(pid 반환)
  [2] 다른 ID + 살아있는 pid        -> 통과(None)  ← force 경로의 정상 사용을 막지 않는다
  [3] 같은 ID + 죽은 pid            -> 통과(None)  ← 좀비 state 를 영구 차단으로 오판하지 않는다
  [4] state 파일 없음               -> 통과(None)

_run: python3 scripts/_dup_guard_test.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("mec", os.path.join(PROJ, "scripts/model_engineer_cycle.py"))
mec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mec)

ok_all = True


def chk(label, got, want):
    global ok_all
    ok = got == want
    ok_all &= ok
    print(("PASS " if ok else "FAIL ") + label + f" :: got={got!r} want={want!r}")


tmpdir = tempfile.mkdtemp()
state = os.path.join(tmpdir, "state.json")
mec.STATE = state

# _pid_alive 는 cmdline 에 '_cycle.py' 가 있어야 '사이클'로 인정한다 → 더미를 그렇게 띄운다.
dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "_cycle.py"])
time.sleep(0.5)
try:
    with open(state, "w") as f:
        json.dump({"id": "CG999", "pid": dummy.pid, "started": "2026-11-17T00:00:00+09:00"}, f)
    chk("[1] 같은 ID + 살아있는 pid → 차단", mec._duplicate_running("CG999"), dummy.pid)
    chk("[2] 다른 ID + 살아있는 pid → 통과", mec._duplicate_running("CG998"), None)

    dummy.kill()
    dummy.wait(timeout=5)
    chk("[3] 같은 ID + 죽은 pid → 통과", mec._duplicate_running("CG999"), None)

    os.remove(state)
    chk("[4] state 없음 → 통과", mec._duplicate_running("CG999"), None)
finally:
    if dummy.poll() is None:
        dummy.kill()

print("ALL PASS" if ok_all else "SOME FAILED")
sys.exit(0 if ok_all else 1)
