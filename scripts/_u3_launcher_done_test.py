#!/usr/bin/env python3
"""_u3_launcher_done_test.py — 런처 유지/폐기 판정 자체점검(이 스택엔 pytest 가 없다).

무엇을 막는가(실측 2026-09-29): 패널 npz 는 빌드 끝에 저장되고 스윕은 그 뒤에 돈다. 빌드가
개장 전 컨테이너 timeout(08:35:55)에 겨우 닿는 밤에는 npz 만 남고 스윕 결과가 없다. 종전
판정('npz 존재 = 완료')은 이 밤에 런처를 폐기 → 틱은 est 1410분 때문에 ETA 가드로 U3 를
건너뛰므로 스윕이 아무도 없는 교착이 된다. 판정을 원장 rc=0 으로 바꾼다.

케이스:
  1) 패널 없음 + 런처 생존 → "런처 실행 중"
  2) 패널 있음 + 실패 기록만(137/1) → 완료 아님("런처 실행 중")
  3) 패널 있음 + U3 rc=0 → "런처 불필요"
  4) 패널 없음 + 런처 죽음(dry)  → "시작 필요"
  5) 런처 스크립트 종료 조건에 u3_done_check 호출이 실제로 있는가(정적 확인)
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model_engineer_cycle as m        # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def led(path, recs):
    with open(path, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return path


with tempfile.TemporaryDirectory() as tmp:
    panel = os.path.join(tmp, "panel_995.npz")
    ledger = os.path.join(tmp, "led.jsonl")
    m.PANEL995 = panel
    m.LEDGER = ledger
    m.launcher_alive = lambda: True            # 실제 프로세스 상태와 무관하게 분기만 본다

    # 1) 패널 없음
    r1 = m.ensure_launcher(dry=True)
    check("1 패널 없음 + 런처 생존 → 실행 중", "실행 중" in r1, r1)

    # 2) 패널만 완성 + 실패 기록만
    open(panel, "wb").close()
    led(ledger, [{"id": "U3", "rc": 137}, {"id": "U3", "rc": 1}])
    r2 = m.ensure_launcher(dry=True)
    check("2 패널만 완성(스윕 결과 없음) → 런처 유지", "런처 불필요" not in r2 and "실행 중" in r2, r2)

    # 3) 패널 + rc=0
    led(ledger, [{"id": "U3", "rc": 137}, {"id": "U3", "rc": 0}])
    r3 = m.ensure_launcher(dry=True)
    check("3 패널 + U3 rc=0 → 런처 불필요", "런처 불필요" in r3, r3)

    # 4) 패널 없음 + 런처 죽음
    os.remove(panel)
    m.launcher_alive = lambda: False
    r4 = m.ensure_launcher(dry=True)
    check("4 패널 없음 + 런처 죽음(dry) → 시작 필요", "시작 필요" in r4, r4)

    # 5) 런처 스크립트가 같은 판정을 쓰는가
    sh = open(os.path.join(HERE, "u3_launcher.sh"), encoding="utf-8").read()
    check("5 u3_launcher.sh 가 u3_done_check 로 종료 판정",
          "u3_done_check.py" in sh and "panel_995.npz 완성 + U3 rc=0 기록 — 런처 종료" in sh)

print(f"\n{'ALL PASS' if not FAILED else str(len(FAILED)) + ' FAIL'}")
sys.exit(1 if FAILED else 0)
