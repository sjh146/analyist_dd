#!/usr/bin/env python3
"""_patch_backlog_lb2 — LB2(rank × 라벨 스무딩 결합) pending 추가 + LB1 결과 기록.

LB1 실측(2026-09-28 04:21, 5폴드×5시드, 같은 런 A/B, panel_420_asofpatch):
  LS_quant_q30_h5(대조군) 0.5414 ± 0.0308 [0.5233 0.5201 0.5606 0.5926 0.5103]
  LB_smooth_q30_h5       0.5467 ± 0.0173 [0.5310 0.5595 0.5431 0.5730 0.5270]  Δ+0.0053 (3/5 폴드 승)
  LB_voladj_q30_h5       0.5381 ± 0.0219 [0.5056 0.5430 0.5679 0.5227 0.5516]  Δ−0.0033 (3/5)
→ 판정: 둘 다 노이즈(문턱 +0.02 미달). 다만 스무딩은 폴드 std 를 −44%(0.0308→0.0173),
  최악 폴드 +0.0167(0.5103→0.5270) 로 **분산을 줄였다** — 평균이 아니라 안정성 레버다.
"""
import json
import os
from datetime import datetime

PROJ = "/home/jhshi/analyist_dd"
PATH = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")
b = json.load(open(PATH, encoding="utf-8"))
items = {i["id"]: i for i in b["items"]}
now = datetime.now().astimezone().replace(microsecond=0).isoformat()

lb1 = items.get("LB1")
if lb1:
    lb1["status"] = "done"
    lb1["result"] = {
        "verdict": "노이즈",
        "detail": ("가설 LB_smooth_q30_h5 0.5467±0.0173 vs 대조군 LS_quant_q30_h5 0.5414±0.0308 "
                   "→ Δ+0.0053 (폴드 3/5 승) · LB_voladj_q30_h5 0.5381±0.0219 → Δ−0.0033 (3/5). "
                   "스무딩은 평균 이득 없음, 다만 폴드 std −44%·최악 폴드 +0.0167."),
        "delta": 0.0053,
        "per_exp": {
            "LS_quant_q30_h5": {"mean": 0.5414, "std": 0.0308,
                                "folds": [0.5233, 0.5201, 0.5606, 0.5926, 0.5103]},
            "LB_smooth_q30_h5": {"mean": 0.5467, "std": 0.0173,
                                 "folds": [0.5310, 0.5595, 0.5431, 0.5730, 0.5270]},
            "LB_voladj_q30_h5": {"mean": 0.5381, "std": 0.0219,
                                 "folds": [0.5056, 0.5430, 0.5679, 0.5227, 0.5516]},
        },
        "rc": 0,
    }
    lb1["closed_at"] = now

if "LB2" not in items:
    b["items"].append({
        "id": "LB2",
        "title": "가산성 확인: 횡단면 rank 변환 × 라벨 스무딩 (약한 양(+) 두 방향의 결합)",
        "status": "pending",
        "priority": 2,
        "affects_model": True,
        "baseline": {"value": 0.5406, "source": "panel_420_asofpatch · 5폴드 확장창 기록 기준선"},
        "hypothesis": ("전처리(rank, TR1 Δ+0.0107)와 라벨(스무딩, LB1 Δ+0.0053·폴드 std −44%)은 "
                       "메커니즘이 달라 결합이 가산적이면 Δ+0.015 근처가 나올 수 있다. 비가산이면 "
                       "조정 축 전체를 닫고 데이터 축으로 넘어간다."),
        "evidence": ("TR2(rank×depth1)는 비가산이었다: Δ+0.0060 < rank 단독 +0.0107. 라벨 축은 "
                     "아직 어떤 조합으로도 측정된 적이 없다. 두 축 모두 같은 런 대조군에서 부호가 "
                     "양(+)이었고 각각 폴드 3/5 승."),
        "method": ("같은 패널·같은 런 3 arm: LS_quant_q30_h5(대조군) / TR_rank_h5 / "
                   "TR_rank_LBsmooth_h5, 5폴드×5시드."),
        "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 7200 "
                    "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                    "--days 420 --limit 50 --folds 5 --seeds 5 --only "
                    "LS_quant_q30_h5,TR_rank_h5,TR_rank_LBsmooth_h5'"),
        "check": "python3 scripts/model_engineer_cycle.py --status",
        "check_target": {"op": ">=", "value": 1},
        "metric": "wf_sweep_summary",
        "arm": "TR_rank_LBsmooth_h5",
        "counterfactual": "LS_quant_q30_h5 (같은 런·같은 패널 대조군)",
        "cost": "약 9분(LB1 3config·5폴드×5시드 실측 8.8분, 부하 경쟁 포함)",
        "est_minutes": 30,
        "success": "결합 arm 이 대조군 대비 +0.02 이상(폴드승률 ≥0.8) → 조정 축 유지. 미달이면 조정 축 종료.",
        "note": "조정(피처·HP·유니버스·라벨) 축의 마지막 실험으로 등록한다. 미달이면 '데이터 축' 승인 요청으로 올린다.",
    })

b["updated_at"] = now
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=2)
print("updated:", now, "| LB1 done, LB2 pending")
