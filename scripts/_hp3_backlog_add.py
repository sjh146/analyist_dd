#!/usr/bin/env python3
"""백로그에 HP3(앙상블 구성 축) 추가 + HP1/HP2 결론 기록 (2026-09-26 틱 2).

HP1(5폴드×3시드): d2 0.5477 > d3 0.5469 > d4 0.5412 > d6 0.5344 — 전부 문턱 +0.02 미달.
HP2(5폴드×5시드): d1 0.5500 · d2+강정규화 0.5491 · d2 0.5470 · d3+강정규화 0.5467 · d2·lr0.08 0.5460
  vs 대조군 0.5414 → Δ+0.0046~+0.0086, 다시 문턱 미달. **HP 축은 재현되지만 폭이 작다**(닫음).
→ 다음 축: 앙상블 구성(모델 집합·가중 방식). 이 스택에서 미검증.
"""
import json
import os
import shutil
import time

P = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "docs/QUANT_MODEL_BACKLOG.json"))
with open(P, encoding="utf-8") as f:
    b = json.load(f)
by = {i["id"]: i for i in b["items"]}

PANEL = "/app/app/models/wf/panel_420_asofpatch.npz"
ONLY = "LS_quant_q30_h5,HP_d1_lr05_h5,EN_equal_h5,EN_drop_cat_h5,EN_d2_equal_h5,EN_d1_dropcat_h5"

# ① HP1 결론 기록(원장에 이미 결과가 있다 — 백로그에는 해석만 남긴다)
if "HP1" in by:
    by["HP1"]["status"] = "done"
    by["HP1"]["note"] = ((by["HP1"].get("note") or "") +
                         " | 2026-09-26 판정: 노이즈. 폴드평균 d2·lr0.05 0.5477±0.0473 · "
                         "d3·lr0.03 0.5469±0.0429 · d4(기준) 0.5412±0.0327 · d6·lr0.02 0.5344±0.0271 "
                         "→ depth 4수준 **단조 감소**(얕을수록 좋음)이나 최대 Δ+0.0065 로 문턱 미달. "
                         "curated 게이트 대조: CO_core30 0.5355 · CO_core_all 0.5263 vs 무게이트 0.5412 "
                         "→ 프로덕션 48피처 게이트를 켜면 −0.015(5폴드 실측).").strip()

item = {
    "id": "HP3",
    "title": "앙상블 구성 축: 가중 방식(균등 vs val-AUC 가중) · 모델 집합(catboost 제외)",
    "status": "pending",
    "priority": 1,
    "arm": "EN_equal_h5",
    "counterfactual": "LS_quant_q30_h5",
    "hypothesis": (
        "3종 소프트보팅의 가중치는 작은 검증분할에서 계산된 (val AUC − 0.5) 비례값이라 그 자체가 "
        "노이즈원이다(실측 로그: catboost 0.078 · lightgbm 0.057 · xgboost 0.29). 가중을 걷어내고 "
        "균등평균하면 폴드 평균이 +0.02 이상 오르는가? 부수: 최약 모델(catboost) 제거, 그리고 "
        "HP2 최고 설정(depth1·depth2)과의 결합."),
    "evidence": (
        "HP1·HP2 실측: depth 를 낮추면 +0.005~+0.009 재현(문턱 미달) → 단일모델 쪽엔 남은 폭이 작다. "
        "앙상블 구성(모델 집합·가중)은 이 스택에서 **한 번도 검증된 적 없다**. "
        "스모크(folds=2·seeds=1, 판정 불가): EN_equal 0.5436 · EN_drop_cat 0.5395 vs 기준 0.5425. "
        "구현 검증: EN_drop_cat 에서 'Training catboost' 0회·xgboost 4회(2폴드×2시드) 확인."),
    "command": (f"docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 7200 "
                f"python -u scripts/wf_label_sweep.py --panel {PANEL} --folds 5 --seeds 5 "
                f"--only {ONLY}'"),
    "metric": "wf_sweep_summary",
    "success": "arm(EN_equal_h5) 폴드 평균이 대조군(LS_quant_q30_h5, 같은 런) 대비 +0.02 이상",
    "expected": "스모크 방향은 +0.001 수준 — 낮은 기대. 실패하면 '모델·앙상블 축 종료'로 기록",
    "cost": "약 18분 (6 config × 5폴드 × 5시드 = 150 학습)",
    "est_minutes": 50,
    "risk": ("앙상블 조작은 wf_label_sweep 프로세스 안에서만 ml.EnsembleModel 을 서브클래스로 "
             "바꿔 적용한다(공유 모듈·프로덕션 경로 무변경). 다중비교 방지를 위해 arm 만 사전등록, "
             "나머지 EN_* 는 탐색으로만 보고한다."),
    "note": ("이 축이 실패하면 남은 후보는 ① 프로덕션 게이트 완화(CO_* 실측 −0.015, 내 소유 아님 "
             "→ 승인 필요) ② 판정 지표를 pooled → 날짜별 횡단면 AUC 로 전환(기준선 재설정 필요) "
             "③ 리서처 백필(주가 이력이 2025-06-16 부터라 행 수 확대 불가 — DB 실측)."),
}
if "HP3" in by:
    by["HP3"].update(item)
    print("HP3 갱신")
else:
    b["items"].append(item)
    print("HP3 추가")

shutil.copy(P, P + ".bak_" + time.strftime("%Y%m%d-%H%M%S"))
b["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
with open(P, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
print("pending:", [i["id"] for i in b["items"] if i.get("status") == "pending"])
