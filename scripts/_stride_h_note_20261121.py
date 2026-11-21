#!/usr/bin/env python3
"""STRIDE_H: 경고 배선 완료 → 의무화(하드 실패)는 별도 승인 대상으로 표시."""
import json
from datetime import datetime, timezone, timedelta

PATH = "docs/QUANT_MODEL_BACKLOG.json"
KST = timezone(timedelta(hours=9))
bl = json.load(open(PATH))
items = bl["items"] if isinstance(bl, dict) else bl
for it in items:
    if it.get("id") != "STRIDE_H":
        continue
    it["status"] = "needs_setup"
    it["setup_needed"] = (
        "1단(완료): fillable_topk_expectancy.py 출력 JSON 에 stride_warning 필드 배선 — "
        "h>1 & stride<h 면 '보유창 중첩으로 t 낙관적' 문구를 남긴다(기본값 불변: 기존 수치와 조용히 달라지지 않게). "
        "2단(승인): 하드 실패로 의무화하려면 기존 머니 기준선 전부를 stride=h 로 재측정해야 한다 — "
        "기준선/프로토콜 변경이라 리뷰보드 승인 대상."
    )
    it["note_20261121"] = (
        "2026-11-21: '--stride' 옵션은 있었지만 기본 stride=1 로 조용히 지나갔다(경고 0). "
        "경고 배선 후 needs_setup 로 내림 — 의무화는 기준선 재측정 동반 필요."
    )
    bl["updated"] = datetime.now(KST).isoformat(timespec="seconds")
    json.dump(bl, open(PATH, "w"), ensure_ascii=False, indent=2)
    print("changed: STRIDE_H")
    break
