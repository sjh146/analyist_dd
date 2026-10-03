#!/usr/bin/env python3
"""CG87 백로그 등록 — 청정 패널(prod200) 등록 프로토콜 기준선 참조치 확정."""
import datetime
import json

PATH = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"

d = json.load(open(PATH, encoding="utf-8"))
assert not any(i.get("id") == "CG87" for i in d["items"]), "CG87 already exists"

item = {
    "id": "CG87",
    "priority": 3,
    "affects_model": True,
    "title": "청정 패널(prod200)에서 등록 프로토콜 기준선 참조치 확정 — 게이트 ON 대조군 vs 게이트 OFF 경로 (5폴드×5시드·전 유니버스)",
    "status": "pending",
    "metric": "wf_sweep_summary",
    "arm": "CO_core30_h5 (게이트 ON 대조군 = 승격 경로 표준 프로토콜)",
    "counterfactual": "LS_quant_q30_h5 (게이트 OFF = 기록 기준선 0.5406 이 나온 경로)",
    "hypothesis": (
        "등록 기준선 0.5406 은 CG63 as-of 수리 이전의 누수 패널(panel_420_asofpatch 계열 — "
        "value_per/pbr/quality_* 종목당 유니크 1~2, CG85 가 LEAKY 로 판정) 위 49종목 값이다. "
        "누수를 제거하면 그 값은 재현되지 않아야 하며, 청정 패널(panel_prod200·200종목·누수 0)에서 "
        "등록 프로토콜(게이트 ON·분위0.30 h5·5폴드×5시드·전 유니버스)의 참조치는 그보다 낮을 개연성이 있다. "
        "이 참조치를 확정해야 북극성 목표(기준선 +0.02)가 '누수 위의 목표'였는지 판정할 수 있다. "
        "CG86 은 같은 패널에서 3시드·30종목 구간판(US_*)만 재서 등록 프로토콜 참조치가 없다(US 평균 ≈0.5179)."
    ),
    "command": (
        "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 7200 python -u "
        "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_prod200.npz --folds 5 --seeds 5 "
        "--only LS_quant_q30_h5,CO_core30_h5,CO_smooth_d1_h5 "
        "--out /app/reports/overnight/cg87_sweep.jsonl --summary-out /app/reports/overnight/cg87_summary.json'"
    ),
    "success": (
        "① 청정 패널 등록 프로토콜 참조치(CO_core30_h5 자기폴드 평균)를 확정해 원장에 남긴다 "
        "② 게이트 비용(짝 Δ = CO_core30_h5 − LS_quant_q30_h5)을 청정 패널에서 재측정(누수판 −0.0051 과 비교) "
        "③ 참조치가 기록 기준선 0.5406 대비 −0.02 이상 낮으면 '기준선 재등록' 승인 요청의 실측 근거로 올린다. "
        "참조치 자체는 성능 판정이 아니라 계측기 값이다(진단 항목 — CG81 선례)."
    ),
    "expected": (
        "미지. 누수 패널의 top30 은 종목상수 재무(quality_roa 0.5534·quality_score 0.5507·value_per 0.5454)로 "
        "채워져 있었다(CG63 진단) → 청정 패널에서는 선별 구성이 시간가변 피처로 바뀌므로 절대값이 낮아질 "
        "개연성이 있다. CG86 의 같은 패널 3시드 구간판(US_*) 평균은 ≈0.5179."
    ),
    "cost": "3 config × 5폴드 × 5시드 = 75 cell · 실측 7~12s/cell → 약 9~15분(경합 시 2배)",
    "est_minutes": 30,
    "note": (
        "2026-10-04 00:0x 신설(주말 자율 세션). 계기: ① CG86(배포가능 arm 스무딩+depth1, 청정 패널) "
        "짝 Δ +0.0064 = 노이즈로 그 축 종결 ② CG85 로 실험 함대가 CLEAN/LEAKY 로 갈렸는데 CLEAN 패널에서 "
        "'등록 프로토콜'을 5시드로 돌린 적이 없다(기록 기준선이 누수판 값) ③ 스킬 규칙 '수리 후에는 수리 전 "
        "수치를 인용하지 마라 — 대조군 재측정이 우선'을 청정 패널에서 이행. ⚠ 이것은 새 레버 탐색이 아니라 "
        "기준선 정합성 계측이다 — '기준선 재등록'은 승인 대상이므로 이 항목은 근거만 만든다."
    ),
}

d["items"].append(item)
d["updated_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

d2 = json.load(open(PATH, encoding="utf-8"))
assert any(i.get("id") == "CG87" for i in d2["items"]), "CG87 not persisted"
print("inserted CG87; total items", len(d2["items"]))
print("status:", next(i["status"] for i in d2["items"] if i["id"] == "CG87"))
