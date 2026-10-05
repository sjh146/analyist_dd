#!/usr/bin/env python3
"""자체점검: next_item 의 depends_on(선행 미완 항목 건너뛰기) 동작.

실측 동기(2026-10-05): CG121 은 CG120 의 덤프가 없으면 즉시 실패한다 — 선행을 선언할 수단이
없어 '사람이 덤프를 확인한 뒤 손으로 pending 승격' 절차에 의존했다. depends_on 을 next_item 이
존중하면 그 절차가 자동이 된다(선행 done 틱에 시작). 이 스크립트는 그 계약을 고정한다.

호스트에서 돈다: python3 scripts/_depends_on_test.py   (docker 불필요)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

FAIL = 0
PASS = 0


def check(name, cond, extra=""):
    global FAIL, PASS
    if cond:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {extra}")


def bl(items):
    return {"items": items}


def item(iid, status="pending", deps=None, command="true", priority=1):
    d = {"id": iid, "status": status, "priority": priority}
    if command is not None:
        d["command"] = command
    if deps:
        d["depends_on"] = deps
    return d


# --- A: 선행이 pending 이면 건너뛴다 --------------------------------------------
b = bl([item("P1"), item("P2", deps=["P1"])])
m.load_ledger = lambda: []
got = m.next_item(b, force=True)
check("A1 선행 pending → 해당 항목 건너뜀", got["id"] == "P1", f"got={got}")
got = m.next_item(bl([item("P2", deps=["P1"])]), force=True)
check("A2 선행 미완 + 다른 pending 없음 → None", got is None, f"got={got}")

# --- B: 선행이 백로그에서 done 이면 통과 ---------------------------------------
b = bl([item("P1", status="done"), item("P2", deps=["P1"])])
got = m.next_item(b, force=True)
check("B1 선행 done → 실행 후보로 반환", got is not None and got["id"] == "P2", f"got={got}")

# --- C: 백로그 status 는 done 이 아닌데(반쪽 상태) 원장에 rc=0 이 있으면 통과 ----
# P1 을 pending 으로 두면 그 자신이 후보가 되어 검사가 흐려진다(테스트 설계 함정) → backlog 로 둔다.
b = bl([item("P1", status="backlog"), item("P2", deps=["P1"])])
m.load_ledger = lambda: [{"id": "P1", "rc": 0}]
got = m.next_item(b, force=True)
check("C1 원장 rc=0 fallback → 실행 후보로 반환", got is not None and got["id"] == "P2", f"got={got}")

# --- C2: 원장에 rc!=0 만 있으면 여전히 대기 ------------------------------------
m.load_ledger = lambda: [{"id": "P1", "rc": 137}]
got = m.next_item(bl([item("P1", status="backlog"), item("P2", deps=["P1"])]), force=True)
check("C2 원장 rc!=0 → 대기", got is None, f"got={got}")

# --- D: depends_on 없는 항목은 종전과 동일 -------------------------------------
m.load_ledger = lambda: None
got = m.next_item(bl([item("X1"), item("X2")]), force=True)
check("D1 depends_on 없음 → priority/id 순 첫 항목", got is not None and got["id"] == "X1", f"got={got}")

# --- E: 선행 충족이지만 command 없음 → 건너뜀 --------------------------------
b = bl([item("P1", status="done"), item("P2", deps=["P1"], command=None)])
got = m.next_item(b, force=True)
check("E1 선행 충족·command 없음 → 건너뜀", got is None, f"got={got}")

# --- F: 다중 선행은 **전부** 완료돼야 한다 ------------------------------------
b = bl([item("P1", status="done"), item("P2", status="pending"),
        item("P3", deps=["P1", "P2"])])
got = m.next_item(b, force=True)
check("F1 다중 선행 중 하나 미완 → 건너뜀", got is not None and got["id"] == "P2", f"got={got}")
b = bl([item("P1", status="done"), item("P2", status="done"), item("P3", deps=["P1", "P2"])])
got = m.next_item(b, force=True)
check("F2 다중 선행 전부 done → 반환", got is not None and got["id"] == "P3", f"got={got}")

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
