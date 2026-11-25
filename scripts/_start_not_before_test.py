"""`--start` 가 not_before 를 존중하는지 자체점검 (실측 2026-11-26 01:11 사고의 회귀 테스트).

사고: next_item 은 not_before 를 존중해 CG464(not_before 2026-12-14)를 틱에서 건너뛰는데,
수동 `--start CG464` 경로에는 가드가 없어 6시간짜리 빌드가 즉시 기동됐다(ETA 373분).
12-14 이전 빌드는 세션이 2개만 늘어 목적(CG443 '새 세션 ≥+15')을 못 채우고, 다음 빌드에서
페어수가 달라져 체크포인트도 폐기된다.

실행: python3 scripts/_start_not_before_test.py
"""
import importlib.util
import os
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)

spec = importlib.util.spec_from_file_location(
    "mec", os.path.join(HERE, "model_engineer_cycle.py"))
mec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mec)

fails = []


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f" — {extra}" if extra else ""))
    if not cond:
        fails.append(name)


def judge():
    future = (mec.now_kst() + timedelta(days=30)).isoformat()
    past = (mec.now_kst() - timedelta(days=30)).isoformat()
    check("미래 not_before → 막는다", mec._not_before_blocks({"not_before": future})[0] is True)
    check("과거 not_before → 막지 않는다", mec._not_before_blocks({"not_before": past})[0] is False)
    check("not_before 없음 → 막지 않는다", mec._not_before_blocks({})[0] is False)
    check("item None → 막지 않는다", mec._not_before_blocks(None)[0] is False)
    check("형식 오류 → 막지 않는다(틱과 동일 동작)",
          mec._not_before_blocks({"not_before": "내일"})[0] is False)
    check("naive 2099 → KST 해석으로 막는다",
          mec._not_before_blocks({"not_before": "2099-01-01T00:00:00"})[0] is True)
    check("naive 1999 → 막지 않는다",
          mec._not_before_blocks({"not_before": "1999-01-01T00:00:00"})[0] is False)


def refusal():
    orig = mec.load_backlog
    mec.load_backlog = lambda: {"items": [
        {"id": "TEST_NB_FUTURE", "not_before": "2099-01-01T00:00:00+09:00", "command": "true"},
    ]}
    try:
        had_pid = os.path.exists(mec.PIDFILE)
        had_state = os.path.exists(mec.STATE)
        rc = mec.start_background("TEST_NB_FUTURE")
        check("미래 항목 --start → rc=3(기동 거부)", rc == 3, f"rc={rc}")
        check("거부 시 state.json 을 만들지 않는다", os.path.exists(mec.STATE) == had_state)
        check("거부 시 running.pid 를 만들지 않는다", os.path.exists(mec.PIDFILE) == had_pid)
    finally:
        mec.load_backlog = orig


def real_backlog():
    items = (mec.load_backlog() or {}).get("items") or []
    cg464 = next((i for i in items if i.get("id") == "CG464"), None)
    if cg464:
        blocked, nb = mec._not_before_blocks(cg464)
        print(f"INFO CG464 not_before={nb} · 현재 막힘={blocked}")
    else:
        print("INFO CG464 항목 없음(이미 종결/이동)")


judge()
refusal()
real_backlog()

print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
sys.exit(0 if not fails else 1)
