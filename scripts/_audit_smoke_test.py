#!/usr/bin/env python3
"""감사 스크립트 4종 스모크 테스트 — 이 스택에는 pytest 가 없다(순수 파이썬 자체점검 규약).

검사: 각 스크립트가 허용 종료코드(0 정상 / 2 이상 / 3 사람단계)로 끝나고, 산출 JSON 이
필수 키를 갖는가. 실패하면 rc=1.

실행: python3 scripts/_audit_smoke_test.py
"""
import glob
import json
import os
import subprocess
import sys

REPO = "/home/jhshi/analyist_dd"
CASES = [
    ("scripts/audit_protocol_lock.py", "reports/locks/lock_audit_*.json",
     ("violations", "queue", "info", "status")),
    ("scripts/audit_measure.py", "reports/audit/measure_*.json",
     ("issues", "info", "status")),
    ("scripts/audit_path_daily.py", "reports/audit/path_*.json",
     ("issues", "info", "status")),
    ("scripts/audit_safety.py", "reports/safety/safety_*.json",
     ("alerts", "human_steps", "info", "status")),
]
FAILS = []


def check(name, cond, note=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {note}")
    if not cond:
        FAILS.append(f"{name} {note}")


def main():
    for script, pat, keys in CASES:
        print(f"[{os.path.basename(script)}]")
        r = subprocess.run([sys.executable, os.path.join(REPO, script)],
                           capture_output=True, text=True, timeout=300, cwd=REPO)
        check("종료코드 허용(0/2/3)", r.returncode in (0, 2, 3), f"rc={r.returncode}")
        files = sorted(glob.glob(os.path.join(REPO, pat)), key=os.path.getmtime)
        check("산출 JSON 존재", bool(files), pat)
        if files:
            try:
                d = json.load(open(files[-1], encoding="utf-8"))
                missing = [k for k in keys if k not in d]
                check("필수 키", not missing, f"누락={missing}" if missing else "")
                check("status 값", d.get("status") in ("ok", "warn", "fail", "issues", "alerts",
                                                        "violations", "human_step"), str(d.get("status")))
            except Exception as e:  # noqa: BLE001
                check("JSON 파싱", False, repr(e)[:80])
    print(f"\n{'ALL PASS' if not FAILS else 'FAIL ' + str(len(FAILS))}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
