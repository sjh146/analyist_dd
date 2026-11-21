#!/usr/bin/env python3
"""큐 위생(2026-11-21): command 없는 priority-1 pending 3건을 실행가능 상태로 정리.

실측: next_item 이 매 틱 `CG101/CG194 는 pending 인데 command 가 없다 → 건너뜀` 을 찍는다.
pending 인데 실행 불가면 priority 정렬에서 계속 1순위 자리를 차지해 큐가 무엇을 기다리는지
보이지 않는다(구동기 규칙 5: 판정·상태는 자기신고가 아니라 구조로 남긴다).

- CG101(인트라데이 축 개척) → needs_setup + gated_by CG388: 같은 축의 현재 차량은
  CG197(패널 A/B) 과 CG388(11-26 확정 판정)이다. 중복 등록을 남겨두면 같은 표본을 두 번 태운다.
- CG194(순기대를 실계좌 체결·청산 기록으로) → needs_setup: 청산 기록 0건은 실행이 아니라 적재가
  선행이다(트레이더 경로 소유). 셋업 문구에 선행 조건을 박아 둔다.
- FS1(피처 스토어 부활) → backlog: 설계·구현 전 단계(현재 feature_values 0행)라 '실행가능 pending'이 아니다.
"""
import json
from datetime import datetime, timezone, timedelta

PATH = "docs/QUANT_MODEL_BACKLOG.json"
KST = timezone(timedelta(hours=9))
bl = json.load(open(PATH))
items = bl["items"] if isinstance(bl, dict) else bl
by = {i["id"]: i for i in items}
changed = []

if "CG101" in by and not by["CG101"].get("command"):
    it = by["CG101"]
    it["status"] = "needs_setup"
    it["gated_by"] = "CG388"
    it["setup_needed"] = (
        "인트라데이 축의 현재 차량은 CG197(패널 A/B)과 CG388(2026-11-26 확정 판정, n_dates>=20)이다. "
        "이 항목은 그 둘로 흡수 — 별도 실행 없이 CG388 판정을 기다린다(중복 실행 금지)."
    )
    it["note_20261121"] = "2026-11-21 큐 위생: command 부재 + CG388/CG197 과 중복 → needs_setup 로 내림."
    changed.append("CG101")

if "CG194" in by and not by["CG194"].get("command"):
    it = by["CG194"]
    it["status"] = "needs_setup"
    it["setup_needed"] = (
        "선행: 실계좌 청산 기록 적재(현재 청산 기록 0건). 순기대를 가정 경로가 아니라 체결·청산 "
        "journal 로 계산하려면 트레이더 경로가 청산 행을 남겨야 한다 — 그 적재가 확인된 뒤 "
        "같은 세션 집합으로 재계산 런을 등록한다."
    )
    it["note_20261121"] = "2026-11-21 큐 위생: command 부재 → needs_setup(선행=청산 기록 적재)."
    changed.append("CG194")

if "FS1" in by and not by["FS1"].get("command"):
    it = by["FS1"]
    it["status"] = "backlog"
    it["note_20261121"] = (
        "2026-11-21 큐 위생: feature_store.feature_values 0행 · 미사용 → '실행가능 pending'이 아니라 "
        "설계/구현 대상이라 backlog 로 내림. 구현 시 재학습 비용 절감 효과를 먼저 계측할 것."
    )
    changed.append("FS1")

if changed:
    bl["updated"] = datetime.now(KST).isoformat(timespec="seconds")
    json.dump(bl, open(PATH, "w"), ensure_ascii=False, indent=2)
print("changed:", changed)
