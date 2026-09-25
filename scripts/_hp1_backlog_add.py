#!/usr/bin/env python3
"""백로그에 RB3(하이퍼파라미터 축 + curated 게이트 대조) 추가 (2026-09-26 틱).

근거(실측, 같은 날 스모크 folds=2/seeds=1):
  · HP_d2_lr05_h5 0.5554 vs 기준 LS_quant_q30_h5 0.5425 (Δ+0.0129) — 스모크라 판정 불가.
  · CO_core_all_h5 0.5119 · CO_core30_h5 0.5216 — core48 로 좁히면 **악화**.
  · 계측 실측: wf_label_sweep 은 curated 게이트를 몽키패치로 꺼 둔다(실효피처 = 선별 수).
    → 스윕 AUC 는 '게이트 없는 210피처 풀' 값이라 프로덕션 챔피언(48피처)과 직접 비교 불가.
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
ONLY = "LS_quant_q30_h5,HP_d2_lr05_h5,HP_d3_lr03_h5,HP_d6_lr02_h5,CO_core30_h5,CO_core_all_h5"
item = {
    "id": "HP1",
    "title": "하이퍼파라미터 축 스윕(depth·lr) + curated 게이트 대조 (6 config 동일 런)",
    "status": "pending",
    "priority": 1,
    "arm": "HP_d2_lr05_h5",
    "counterfactual": "LS_quant_q30_h5",
    "hypothesis": (
        "모델 레시피(depth=4·lr=0.03·1500트리)는 이 스택에서 한 번도 스윕된 적 없다. "
        "폴드당 학습행이 1,230행뿐인 소표본이므로 얕은 트리·큰 학습률이 과적합을 줄여 AUC 를 "
        "올리는가? 부수 질문: 프로덕션 트레이너의 CORE_FEATURES(48) 게이트를 켜면(CO_*) "
        "게이트 없는 풀(기준선)보다 나은가?"),
    "evidence": (
        "스모크(folds=2·seeds=1, 판정 불가): HP_d2_lr05 0.5554 vs 기준 0.5425 (+0.0129) · "
        "CO_core30 0.5216 · CO_core_all 0.5119 → 게이트를 켜면 0.02~0.03 악화. "
        "계측 추가: wf_label_sweep 은 tc.select_curated_features 를 항등함수로 몽키패치해 "
        "게이트가 꺼져 있고(실효피처=선별 수 [30]·[48]), 실제 탈락 규모는 이름 기준 약 60%다."),
    "command": (f"docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 7200 "
                f"python -u scripts/wf_label_sweep.py --panel {PANEL} --folds 5 --seeds 3 "
                f"--only {ONLY}'"),
    "metric": "wf_sweep_summary",
    "success": "arm(HP_d2_lr05_h5) 폴드 평균이 대조군(LS_quant_q30_h5) 대비 +0.02 이상",
    "expected": "미지 — 스모크에서 +0.0129 방향, 폴드 std ±0.03 이라 5폴드로 확인 필요",
    "cost": "약 10~15분 (6 config × 5폴드 × 3시드 = 90 학습. RB1 실측 60학습=6.0분)",
    "est_minutes": 30,
    "risk": ("하이퍼파라미터를 여러 개 한 런에 넣으면 다중비교 위험이 있다 → arm 을 사전 "
             "등록(HP_d2_lr05_h5)했고, 나머지 HP_* 는 탐색으로만 보고하며 '개선' 판정에 쓰지 않는다. "
             "n_estimators 는 lightgbm 에만 적용된다(xgboost 800·catboost 300 고정)."),
    "note": ("CO_* 는 게이트 대조군이다: 프로덕션 챔피언 경로는 48피처 게이트를 쓰므로, "
             "게이트가 -0.02~-0.03 이면 '게이트 완화'가 승인 대상 후보다(승격 아님·별도 판단). "
             "| RB1 정정: RB1 의 'top30 중 종목상수 11~16개' 관찰은 게이트를 끈 풀 기준이며, "
             "실효피처 계측(n_effective_features)을 요약 JSON 에 추가했다."),
}

if "HP1" in by:
    by["HP1"].update(item)
    print("HP1 갱신")
else:
    b["items"].append(item)
    print("HP1 추가")

# pending 이 뒤로 밀리지 않게 정렬 우선순위 재확인(priority 작을수록 먼저)
shutil.copy(P, P + ".bak_" + time.strftime("%Y%m%d-%H%M%S"))
b["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
with open(P, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
print("저장:", P)
print("pending:", [i["id"] for i in b["items"] if i.get("status") == "pending"])
