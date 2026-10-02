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
    tz = m.now_kst().tzinfo
    # ⚠ 날짜 의존 제거(2026-10-03): 종전엔 "지금 시각"에서 재생성 창까지의 거리를 가정했다 —
    # 주말(토 04:00 → 다음 재생성 월 20:00 = 64h 뒤)엔 est 1410분(23.5h)이 창을 넘지 않아
    # **정당하게 통과**하는데도 테스트가 FAIL 을 켰다(스킬 교훈 '달력 기대값이 낡으면 빨간불'
    # 과 같은 클래스 — 코드 회귀로 오진 금지). next_recreate/next_market_open 을 고정해
    # 시각과 무관하게 검증한다. probe 변수(미사용)도 제거.
    orig_rec, orig_mo = m.next_recreate, m.next_market_open
    base = datetime.now(tz)
    m.next_market_open = lambda *a, **k: None
    try:
        # 케이스 1: 다음 재생성 창이 2시간 뒤 → BIG(23.5h)은 반드시 차단된다.
        m.next_recreate = lambda now=None: base + timedelta(hours=2)
        blocked, why = m.eta_blocks(eta_blocking_item())
        ok_eta = m.eta_blocks({"id": "X", "est_minutes": 1})[0] is False
        check("eta_blocks: est_minutes=1 은 통과", ok_eta)
        check("eta_blocks: est_minutes=1410 차단(재생성 2h 뒤)", blocked is True)

        b = {"items": [eta_blocking_item(), small_item()]}
        picked = m.next_item(b)
        check("next_item(force=False): ETA 차단 BIG 을 건너뛰고 SMALL 선택",
              (picked or {}).get("id") == "SMALL")

        picked_f = m.next_item(b, force=True)
        check("next_item(force=True): BIG 도 후보로 반환(강행 경로 유지)",
              (picked_f or {}).get("id") == "BIG")

        # 케이스 2: 재생성 창이 멀면(주말 등) BIG 도 통과한다 — 주말엔 ETA 가드가 열려 있어
        # 긴 항목이 사람 감시 없이 밤새 돌 수 있다(스킬 교훈). 이 사실을 테스트로 고정한다.
        m.next_recreate = lambda now=None: base + timedelta(days=5)
        check("eta_blocks: 재생성 5일 뒤면 1410 통과(주말 개방 — 주의)",
              m.eta_blocks(eta_blocking_item())[0] is False)
    finally:
        m.next_recreate, m.next_market_open = orig_rec, orig_mo

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
