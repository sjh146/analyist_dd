#!/usr/bin/env python3
"""자체점검 — scripts/data_axis_readiness.py 의 승격 판정 로직(DB 없이).

핵심 위험: 준비되지 않은 축을 승격하면 규칙 위반(선행 미충족 실험 = 즉시 실패·원장 잡음)이고,
command/counterfactual/success 가 빈 항목을 승격하면 구동기 next_item 이 'pending 인데 command 없다'
로 영구 건너뛴다. 두 경우를 못박는다.
"""
import copy
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("dar", os.path.join(HERE, "data_axis_readiness.py"))
dar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dar)

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# 1) needs_fields_ok — 필수 3필드 누락 탐지
full = {"command": "x", "counterfactual": "y", "success": "z"}
check("필수 3필드 충족 → 누락 0", dar.needs_fields_ok(full) == [])
check("success 누락 탐지", dar.needs_fields_ok({"command": "x", "counterfactual": "y"}) == ["success"])

# 2) promote — ready 축만 승격, BLOCKED 는 그대로, 필드 미비는 보류
axes = [
    {"axis": "intraday", "items": ["CG129", "CG101"], "ready": False},
    {"axis": "short_selling", "items": ["CG141"], "ready": True},
    {"axis": "news::news_events", "items": ["CG73"], "ready": True},
]
bl = {"items": [
    {"id": "CG129", "status": "needs_setup", **full},      # BLOCKED → 손대지 않음
    {"id": "CG141", "status": "needs_setup", **full},      # READY + 필드 충족 → pending
    {"id": "CG73", "status": "needs_setup", "command": "c", "success": "s"},  # READY 지만 필드 미비 → 보류
    {"id": "CG101", "status": "needs_setup", **full},      # BLOCKED → 손대지 않음
]}
ch = dar.promote(axes, bl)
ids = {it["id"]: it["status"] for it in bl["items"]}
check("BLOCKED 는 승격 안 됨(CG129)", ids["CG129"] == "needs_setup")
check("READY+필드충족 → pending(CG141)", ids["CG141"] == "pending")
check("READY+필드미비 → 보류(CG73)", ids["CG73"] == "needs_setup")
check("BLOCKED 는 승격 안 됨(CG101)", ids["CG101"] == "needs_setup")
check("변경 목록에 승격 1건·보류 1건", sorted(ch) == [("CG141", "pending"), ("CG73", "보류(missing counterfactual)")])

# 3) 준비된 축이 없으면 promote 는 no-op(부분 변경 금지)
bl2 = {"items": [{"id": "CG141", "status": "needs_setup", **full}]}
ch2 = dar.promote([{"axis": "short_selling", "items": ["CG141"], "ready": False}], bl2)
check("ready 0건 → 변경 0건", ch2 == [] and bl2["items"][0]["status"] == "needs_setup")

# 4) evaluate 문턱 상수 존재(항목 note 의 값과 일치해야 함)
check("인트라데이 문턱 = 전구간 봉수 300", dar.TH["intraday_bars_per_day"] == 300)
check("커버리지 문턱 = 0.75", dar.TH["panel_cover_frac"] == 0.75)

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)} ({len(fails)} fail)")
sys.exit(1 if fails else 0)
