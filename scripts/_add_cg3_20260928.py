#!/usr/bin/env python3
"""CG3 등록: 게이트 ON 경로에서 보유 방향 전부 결합 → 생산경로 천장 확정 (CG2 파생)."""
import json
import shutil

P = "docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(P, P + ".bak-cg3")
b = json.load(open(P, encoding="utf-8"))
items = b["items"]
by = {i["id"]: i for i in items}

by["CG2"]["status"] = "done"
by["CG2"]["result"] = {
    "verdict": "노이즈(구조 분해 완료)",
    "detail": ("같은 런 5-arm(5폴드×5시드): LS_quant_q30_h5 0.5414±0.0308(게이트OFF·평범) · "
               "TR_rank_LBsmooth_h5 0.5514±0.0107(게이트OFF·rank+스무딩) · CO_core30_h5 0.5363±0.0188"
               "(게이트ON·평범) · CO_rank_smooth_h5 0.5412±0.0165(게이트ON·rank+스무딩) · "
               "CO_rank_h5 0.5098±0.0275(게이트ON·rank만). 분해: 게이트 비용 −0.0051(평범)/−0.0102"
               "(rank+스무딩) · 게이트 내 rank+스무딩 +0.0049 · 게이트 내 rank 단독 −0.0265(파괴적)."),
    "delta": -0.0051,
    "rc": 0,
}

cg3 = {
    "id": "CG3",
    "title": "게이트 ON 경로 천장 확정 — 보유 방향 전부 결합(rank+스무딩+depth1)이 +0.02 에 닿는가",
    "status": "pending",
    "priority": 2,
    "affects_model": True,
    "baseline": {"value": 0.5363, "source": "CG2 같은 런 CO_core30_h5(게이트 ON 평범)"},
    "hypothesis": ("게이트 ON 경로에서 라벨 스무딩 단독과 depth1(게이트 OFF 에서만 +0.0065 검증)을 "
                   "결합하면 문턱 +0.02 에 닿는다. 못 닿으면 조정 축은 생산경로에서 종결되고, 남는 레버는 "
                   "데이터 축(U3 창 2.35배·신규 시점정합 피처)뿐이라고 확정할 수 있다."),
    "evidence": ("CG2 실측: 게이트 비용 −0.0051(평범)·−0.0102(rank+스무딩) → 게이트 OFF 최고 0.5514 가 "
                 "게이트 ON 에서 0.5412. 게이트 ON 최고 기록은 rank+스무딩 0.5412(+0.0049 vs 0.5363). "
                 "게이트 ON 으로 아직 재보지 않은 방향 = 라벨 스무딩 단독 · depth1·lr0.05."),
    "method": ("같은 패널·같은 런 3-arm(5폴드×5시드): CO_core30_h5(대조군) · CO_smooth_h5(스무딩 단독) · "
               "CO_d1_h5(depth1) · CO_rank_smooth_d1_h5(전부 결합). 결합 arm 이 대조군 +0.02 미달이면 "
               "조정 축은 게이트 ON 경로에서 종결."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 5 --only CO_core30_h5,CO_smooth_h5,CO_d1_h5,CO_rank_smooth_d1_h5'"),
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "success": ("CO_rank_smooth_d1_h5 ≥ 0.5563 (게이트 ON 대조군 0.5363 + 0.02) → 생산경로 승격 후보. "
                "미달이면 '조정 축 게이트 ON 종결' 로 기록하고 데이터 축(U3) 단독 레버로 넘어간다."),
    "counterfactual": "CO_core30_h5 (같은 런, 게이트 ON 평범) — 아직 코드에 config 없으면 추가 필요",
    "est_minutes": 15,
    "cost": "100 cell — CG1(75 cell) 2.1분 실측 기준 유휴 3분, 상한 3600s",
    "metric": "wf_sweep_summary",
    "arm": "CO_rank_smooth_d1_h5",
    "caution": "장중 가드는 틱이 막는다. timeout 3600s.",
    "note": "2026-09-28 05:2x 신설. config CO_smooth_h5·CO_d1_h5·CO_rank_smooth_d1_h5 를 wf_label_sweep.py 에 함께 추가했다.",
}
if "CG3" not in by:
    items.append(cg3)
b["updated_at"] = "2026-09-28T05:22:00+09:00"
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG2 → done · CG3 신설 · items:", len(items))
