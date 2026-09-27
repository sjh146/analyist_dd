#!/usr/bin/env python3
"""CG2 등록: 게이트 ON/OFF × rank/스무딩 2×2 분해(같은 런). CG1 결과에서 파생된 후속 실험."""
import json
import shutil

P = "docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(P, P + ".bak-cg2")
b = json.load(open(P, encoding="utf-8"))
items = b["items"]
by = {i["id"]: i for i in items}

cg2 = {
    "id": "CG2",
    "title": "게이트 ON/OFF × rank·스무딩 2×2 분해 — 스윕 이득이 승격 경로에서 사라지는 원인 분리",
    "status": "pending",
    "priority": 2,
    "affects_model": True,
    "baseline": {"value": 0.5414,
                 "source": "CG1 같은 런 대조군 LS_quant_q30_h5(게이트 OFF 평범, 5폴드×5시드)"},
    "hypothesis": ("CG1 에서 게이트를 켜면 스윕 최고 설정의 이득(+0.0100)이 통째로 사라졌다"
                   "(게이트 OFF 0.5514 → 게이트 ON 0.5412). 그 원인이 ① 게이트 자체의 비용인지 "
                   "② rank·스무딩의 효과가 게이트 안에서 조건부로 소멸하는 것인지 분리한다."),
    "evidence": ("CG1 실측(2026-09-28 05:08, 같은 런): LS_quant_q30_h5 0.5414±0.0308(게이트 OFF 평범) · "
                 "TR_rank_LBsmooth_h5 0.5514±0.0107(게이트 OFF 최고) · CO_rank_smooth_h5 0.5412±0.0165(게이트 ON). "
                 "→ 게이트가 −0.0102. 게이트 ON 평범(CO_core30_h5)은 이 런에 없었다(기록값 0.5355, 타 런)."),
    "method": ("같은 패널·같은 런 5-arm: LS_quant_q30_h5(OFF·평범) · TR_rank_LBsmooth_h5(OFF·rank+스무딩) · "
               "CO_core30_h5(ON·평범) · CO_rank_h5(ON·rank) · CO_rank_smooth_h5(ON·rank+스무딩), 5폴드×5시드. "
               "분해: 게이트 비용 = CO_core30_h5 − LS_quant_q30_h5 / 게이트 내 rank 이득 = CO_rank_h5 − CO_core30_h5."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 5 --only LS_quant_q30_h5,TR_rank_LBsmooth_h5,CO_core30_h5,CO_rank_h5,"
                "CO_rank_smooth_h5'"),
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "success": ("|게이트 비용| ≤ 0.01 이면 '게이트 무해 → 스윕 지표는 승격 경로를 대표한다'(그때는 조건부 상쇄), "
                "> 0.02 이면 '승격 경로 자체가 병목' → CORE_FEATURES 게이트 재검토 승인 요청(사람 승인 대상)."),
    "counterfactual": "LS_quant_q30_h5 (같은 런, 게이트 OFF 평범) — 게이트/변환 효과의 공통 기준",
    "est_minutes": 15,
    "cost": "125 cell — CG1(75 cell)이 2.1분이었으므로 유휴 시 3~5분, 경쟁 시 상한 3600s",
    "metric": "wf_sweep_summary",
    "arm": "CO_rank_smooth_h5",
    "caution": "장중 가드는 틱이 막는다. timeout 3600s 로 개장 전 종료.",
}
if "CG2" not in by:
    items.append(cg2)
cg1 = by["CG1"]
cg1["status"] = "done"
cg1["result"] = {
    "verdict": "노이즈(단, 구조적 발견)",
    "detail": ("게이트 ON 최고 설정 CO_rank_smooth_h5 0.5412 vs 같은 런 게이트 OFF 대조군 0.5414 → Δ−0.0002 · "
               "vs 기록 게이트 ON 기준선 0.5355 → Δ+0.0057. **핵심**: 같은 런에서 게이트 OFF 최고 "
               "TR_rank_LBsmooth_h5 0.5514 가 게이트 ON 에서 0.5412 로 떨어져 스윕 이득(+0.0100)이 "
               "게이트(−0.0102)로 전부 상쇄됐다. 17사이클의 '+방향'은 게이트 OFF 경로 값이었다."),
    "delta": -0.0002,
    "rc": 0,
}
b["updated_at"] = "2026-09-28T05:20:00+09:00"
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG1 → done(CG1 실측 기록) · CG2 신설 · items:", len(items))
