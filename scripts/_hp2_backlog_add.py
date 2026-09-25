#!/usr/bin/env python3
"""백로그에 HP2(depth·정규화 후속) 추가 (2026-09-26 틱 2).

근거(실측): HP1 5폴드×3시드 —
  d2·lr0.05 0.5477 / d3·lr0.03 0.5469 / d4·lr0.03(기준) 0.5412 / d6·lr0.02 0.5344 → depth 단조.
  모두 +0.02 문턱 미달이라 '노이즈' 판정이지만, 4수준 단조는 소표본 과적합 방향 신호다.
  스모크(folds=2·seeds=1): HP_d2_reg 0.5573 vs 기준 0.5425 (+0.0148, 판정 불가).
시드 5로 올려 판정 안정성도 함께 본다(HP1 에서 d2 폴드 std 가 0.0473 으로 컸다).
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
ONLY = ("LS_quant_q30_h5,HP_d2_lr05_h5,HP_d2_reg_h5,HP_d1_lr05_h5,"
        "HP_d2_lr08_h5,HP_d3_reg_h5")

item = {
    "id": "HP2",
    "title": "depth·정규화 후속: d1·lr0.08·강정규화(subsample/colsample/mcw/L2) — 5폴드×5시드",
    "status": "pending",
    "priority": 1,
    "arm": "HP_d2_reg_h5",
    "counterfactual": "LS_quant_q30_h5",
    "hypothesis": (
        "폴드당 학습행이 1,230행뿐인 소표본에서 depth 를 낮추고 정규화를 직접 세게 걸면 "
        "(서브샘플 0.6·열샘플 0.5·min_child_weight 10·L2 5) 폴드 평균이 기준선 대비 +0.02 "
        "이상 오르는가? HP1 에서 depth 축은 단조(d2 0.5477 > d3 0.5469 > d4 0.5412 > d6 0.5344)로 "
        "방향은 맞았으나 폭이 +0.0065 였다 — 정규화를 더하면 폭이 커지는가?"),
    "evidence": (
        "HP1 실측(5폴드×3시드, 8.6분): d2·lr0.05 0.5477±0.0473 · d3·lr0.03 0.5469±0.0429 · "
        "d4(기준) 0.5412±0.0327 · d6·lr0.02 0.5344±0.0271 → 4수준 단조. "
        "스모크(folds=2·seeds=1): HP_d2_reg 0.5573 vs 기준 0.5425. "
        "데이터 축 실측: stock_prices 이력이 2025-06-16~2026-09-23 뿐이라(3,934종목·1,078,479행) "
        "패널 --days 를 늘려도 행이 늘지 않는다 → '표본 확대' 레버는 현재 데이터로는 불가."),
    "command": (f"docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 7200 "
                f"python -u scripts/wf_label_sweep.py --panel {PANEL} --folds 5 --seeds 5 "
                f"--only {ONLY}'"),
    "metric": "wf_sweep_summary",
    "success": "arm(HP_d2_reg_h5) 폴드 평균이 대조군(LS_quant_q30_h5, 같은 런·같은 시드) 대비 +0.02 이상",
    "expected": "스모크 방향이면 +0.015 내외 — 문턱 미달이면 depth·정규화 축도 닫고 새 레버 승인으로 올린다",
    "cost": "약 16분 (6 config × 5폴드 × 5시드 = 150 학습)",
    "est_minutes": 45,
    "risk": ("시드를 3→5 로 올렸으므로 기록 기준선(0.5412, seeds=3)과 직접 비교하지 말고 "
             "**같은 런의 대조군**과만 비교한다(구동기 judge_per 가 arm/counterfactual 로 처리). "
             "recipe_extra 는 wf_label_sweep 프로세스 안에서만 tc.apply_hyperparams 를 감싸 "
             "주입한다 — 공유 트레이너·프로덕션 경로는 무변경."),
    "note": ("정규화 키 이름이 모델마다 달라(subsample·colsample_bytree 는 xgb/lgbm, "
             "min_child_weight·reg_lambda 는 xgb, lambda_l2 는 lgbm, l2_leaf_reg 는 catboost) "
             "각 모델이 아는 키만 적용된다 — 적용 여부는 스모크에서 params 로 확인했다."),
}

if "HP2" in by:
    by["HP2"].update(item)
    print("HP2 갱신")
else:
    b["items"].append(item)
    print("HP2 추가")

shutil.copy(P, P + ".bak_" + time.strftime("%Y%m%d-%H%M%S"))
b["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
with open(P, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
print("pending:", [i["id"] for i in b["items"] if i.get("status") == "pending"])
