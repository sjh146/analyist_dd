#!/usr/bin/env python3
"""백로그에 TR2(rank 변환 × depth1 결합) 추가 — 2026-09-26 06:09 틱.

근거(같은 날 같은 프로토콜 실측):
  TR1: TR_rank_h5 0.5519±0.0333 vs 기준 0.5412 → Δ+0.0107 (폴드 전부 >0.52, 최악폴드 0.5075→0.5207)
  HP4: HP_d1_lr05_h5 0.5478±0.0527 vs 기준 0.5413 → Δ+0.0065 (seeds=10 에서도 방향 유지)
둘 다 문턱 +0.02 미달이지만 부호가 양이고 서로 다른 축(전처리 / 모델 용량)이다.
결합이 가산적인지(≈+0.017), 상쇄인지(rank 가 시장레벨을 지우고 depth1 이 저분산만 학습)를 잰다.
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

item = {
    "id": "TR2",
    "title": "결합 축: 횡단면 rank 변환 × depth1·lr0.05 (양(+) 방향 두 개의 가산성 측정)",
    "status": "pending",
    "priority": 1,
    "arm": "TR_rank_d1_h5",
    "counterfactual": "LS_quant_q30_h5",
    "hypothesis": ("TR1 에서 횡단면 rank 변환이 Δ+0.0107, HP 계열에서 depth1·lr0.05 가 Δ+0.0065 로 "
                   "둘 다 양(+)이었다. 서로 다른 축(전처리 vs 모델 용량)이므로 결합하면 +0.02 문턱에 "
                   "근접하거나 넘는가? 상쇄되면 'rank 가 이미 용량 문제를 흡수한다'로 해석한다."),
    "evidence": ("TR1(5폴드×3시드, panel_420_asofpatch): TR_rank 0.5519±0.0333 vs 기준 0.5412±0.0327 "
                 "→ Δ+0.0107 · TR_zscore 0.5365±0.0131 → Δ−0.0047. "
                 "HP4(5폴드×10시드): d1 0.5478±0.0527 vs 0.5413 → Δ+0.0065(방향 재현, 폭은 축소)."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 5 --only LS_quant_q30_h5,TR_rank_h5,TR_rank_d1_h5'"),
    "metric": "wf_sweep_summary",
    "success": "arm(TR_rank_d1_h5) 폴드 평균이 같은 런 대조군 대비 +0.02 이상",
    "expected": "+0.010 ~ +0.018 (가산적이면 문턱 근접, 상쇄면 +0.005 이하)",
    "cost": "약 4분 (3 config × 5폴드 × 5시드 = 75 학습)",
    "est_minutes": 15,
    "risk": ("TR_rank_h5 는 시장레벨 피처를 날짜내 상수로 만들어 정보를 지운다 — 결합 결과가 "
             "기준선 아래로 떨어지면 '두 효과는 독립이 아니다'로 기록하고 결합 축을 닫는다."),
    "note": ("wf_label_sweep.CONFIGS 에 TR_rank_d1_h5 를 추가했다(이 역할 소유 파일). "
             "승격은 하지 않는다 — 문턱을 넘어도 champion_promote --dry-run + 별도 판단이다."),
}
if "TR2" in by:
    by["TR2"].update(item)
    print("TR2 갱신")
else:
    b["items"].append(item)
    print("TR2 추가")

shutil.copy(P, P + ".bak_" + time.strftime("%Y%m%d-%H%M%S"))
b["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
with open(P, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
print("pending:", [i["id"] for i in b["items"] if i.get("status") == "pending"])
