#!/usr/bin/env python3
"""_backlog_status_test — 사이클 종료 후 백로그 status 결정(순수 함수) 자체점검.

대상: `scripts/model_engineer_cycle.backlog_status_after(rc, cause, gate_rc5, n_attempts)`

왜 중요한가(실측 교훈): **인프라 사고는 가설의 결과가 아니다** — 컨테이너 재생성(137)·
타임아웃(124)·빌드 중 피처 코드 변경·패널 락 보류(PanelLockBusy)를 'failed'(=측정된 실패)로
남기면 무개선 카운터와 '새 레버 필요' 판단이 오염된다. 반대로 진짜 실패(rc=1, 다른 원인)는
pending 으로 되돌리면 같은 실험을 영원히 재시도한다.

이 스택엔 pytest 가 없다 → PASS/FAIL 을 출력하고 실패 시 exit 1 (기존 _*_test.py 관례).
호스트에서 그대로 돈다:  python3 scripts/_backlog_status_test.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def main():
    RMAX = m.RETRY_MAX
    f = m.backlog_status_after

    # --- 성공/게이트 판정은 done ---------------------------------------------
    check("1 rc=0 → done", f(0, "", False, 0)[0] == "done")
    check("2 rc=5 + gate_rc5 → done(게이트 판정이지 실패 아님)",
          f(5, "invalid_candidate", True, 0)[0] == "done")
    check("3 rc=5 인데 gate_rc5=False → failed", f(5, "", False, 0)[0] == "failed")

    # --- 인프라 사고 → pending(재시도) ---------------------------------------
    check("4 rc=137(컨테이너 재생성) → pending·'체크포인트 재개'",
          f(137, "SIGKILL", False, 0) == ("pending", "체크포인트 재개"))
    check("5 rc=124(타임아웃) → pending",
          f(124, "timeout", False, 0)[0] == "pending")
    st, note = f(1, "종료코드 1 — 빌드 중 피처 코드 변경 감지 ...", False, 0)
    check("6 rc=1 + 피처 코드 변경 → pending·코드 프리즈 문구",
          st == "pending" and "코드 프리즈" in (note or ""), f"{st} / {note}")
    st, note = f(1, "종료코드 1 — panel_meta.PanelLockBusy: 다른 프로세스가 같은 패널 ...", False, 0)
    check("7 rc=1 + PanelLockBusy → pending·패널 락 문구(가설 실패 아님)",
          st == "pending" and "패널 락" in (note or ""), f"{st} / {note}")

    # --- 재시도 한도 초과 → failed -------------------------------------------
    check(f"8 rc=137 인데 attempts({RMAX}) 도달 → failed",
          f(137, "SIGKILL", False, RMAX)[0] == "failed")
    check("9 PanelLockBusy 라도 한도 도달 → failed",
          f(1, "PanelLockBusy", False, RMAX)[0] == "failed")

    # --- 진짜 실패는 pending 으로 되돌리지 않는다 ----------------------------
    check("10 rc=1(원인 미상) → failed(영구 재시도 금지)",
          f(1, "종료코드 1 — KeyError: 'date'", False, 0)[0] == "failed")
    check("11 rc=2 → failed", f(2, "", False, 0)[0] == "failed")

    # --- 경계: cause=None/빈 문자열 ------------------------------------------
    check("12 cause=None 에서도 예외 없이 failed", f(1, None, False, 0)[0] == "failed")
    check("13 rc=0 이면 cause·attempts 무관하게 done", f(0, None, False, RMAX)[0] == "done")

    # --- 회귀: 명시적 대조(같은 rc 라도 cause 로 갈린다) ----------------------
    a = f(1, "종료코드 1 — PanelLockBusy", False, 0)[0]
    b = f(1, "종료코드 1 — ValueError: bad shape", False, 0)[0]
    check("14 같은 rc=1 이라도 인프라(cause)와 실패(cause)가 갈린다",
          a == "pending" and b == "failed", f"{a} vs {b}")

    n = len(RESULTS)
    nfail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n== {n - nfail}/{n} PASS ==" + ("" if not nfail else f"  ({nfail} FAIL)"))
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
