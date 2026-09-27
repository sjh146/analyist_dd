#!/usr/bin/env python3
"""_next_item_eta_test — next_item 이 ETA 가드에 걸린 항목 때문에 큐를 멈추지 않는지 검증.

실측 배경(2026-09-28 04:0x): U3(est_minutes=1410)가 priority=1 인데 평일엔 예상 종료가
다음 컨테이너 재생성(평일 20:00)을 넘어 execute() 가 rc=3 으로 거부된다. 수정 전 next_item 은
매 틱 U3 만 돌려주므로 **다른 pending 이 영구 대기**했다(굶음). 수정 후에는 ETA 에 걸린 항목을
건너뛰고 다음 후보를 돌려준다.

실행: python3 scripts/_next_item_eta_test.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_engineer_cycle as m  # noqa: E402

FAIL = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAIL.append(name)


def eta_blocking_item():
    """평일 20:00 재생성 창을 확실히 넘는 항목(23.5시간)을 만든다."""
    return {"id": "BIG", "status": "pending", "priority": 1,
            "command": "true", "est_minutes": 1410,
            "title": "긴 항목(재생성 창 초과)"}


def small_item():
    return {"id": "SMALL", "status": "pending", "priority": 9,
            "command": "true", "est_minutes": 5,
            "title": "짧은 항목"}


def main():
    print("=== next_item ETA 굶음 회귀 테스트 ===")
    now = datetime.now(m.now_kst().tzinfo)

    # 케이스 1: 장중이 아닌 평일 밤(다음 재생성 = 내일/다음 평일 20:00)에 BIG 은 걸린다.
    #          오늘 20:00 전 시각을 골라 BIG(23.5h)이 확실히 재생성 창을 넘게 한다.
    probe = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if probe <= now:
        probe = probe + timedelta(days=1)
    blocked, why = m.eta_blocks(eta_blocking_item())
    ok_eta = m.eta_blocks({"id": "X", "est_minutes": 1})[0] is False
    check("eta_blocks: est_minutes=1 은 통과", ok_eta)
    check("eta_blocks: est_minutes=1410 은 차단(현재 시각 기준)", blocked is True)
    if not blocked:
        print("    (참고: 지금 시각에서는 재생성 창까지 23.5h 이상 남아 통과 — 케이스 2로 검증)")

    b = {"items": [eta_blocking_item(), small_item()]}
    picked = m.next_item(b)
    check("next_item(force=False): ETA 차단 BIG 을 건너뛰고 SMALL 선택",
          (picked or {}).get("id") == "SMALL")

    picked_f = m.next_item(b, force=True)
    check("next_item(force=True): BIG 도 후보로 반환(강행 경로 유지)",
          (picked_f or {}).get("id") == "BIG")

    # 케이스 3: 짧은 항목만 있으면 정상 선택(과잉 차단 방지)
    b2 = {"items": [small_item()]}
    check("next_item: 짧은 항목만 있으면 그대로 선택",
          (m.next_item(b2) or {}).get("id") == "SMALL")

    # 케이스 4: command 없는 항목은 여전히 건너뛴다
    b3 = {"items": [dict(small_item(), id="NO_CMD", command=None, priority=0), small_item()]}
    check("next_item: command 없는 항목 건너뜀",
          (m.next_item(b3) or {}).get("id") == "SMALL")

    print(f"\n결과: {len(FAIL)}건 FAIL" if FAIL else "\n전부 PASS")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
