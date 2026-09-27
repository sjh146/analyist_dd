#!/usr/bin/env python3
"""CG4 등록: CG3 최초 문턱 돌파(+0.0219, 게이트 ON 경로)의 10시드 확인."""
import json
import shutil

P = "docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(P, P + ".bak-cg4")
b = json.load(open(P, encoding="utf-8"))
items = b["items"]
by = {i["id"]: i for i in items}

by["CG3"]["status"] = "done"
by["CG3"]["result"] = {
    "verdict": "신호있음 — 17사이클 만의 사전문턱(+0.02) 돌파",
    "detail": ("같은 런·게이트 ON(core48) 5폴드×5시드: CO_core30_h5 0.5363±0.0188(대조군) · "
               "CO_smooth_h5 0.5420±0.0103(+0.0057) · CO_d1_h5 0.5539±0.0442(+0.0176) · "
               "CO_rank_smooth_d1_h5 0.5582±0.0078(+0.0219, min 0.5456, 폴드승률 1.0). "
               "폴드 std 0.0078 은 기록상 최저 — 문턱을 넘으면서 분산도 줄였다. 단 폴드별 짝 비교는 "
               "4/5 승(부호검정 p≈0.19)이라 시드 확대 확인이 필요하다."),
    "delta": 0.0219,
    "rc": 0,
}

cg4 = {
    "id": "CG4",
    "title": "CG3 신호 확인: 게이트 ON 전방향 결합(rank+스무딩+depth1) 10시드 재측정 + 폴드별 짝 비교",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.5363, "source": "CG3 같은 런 CO_core30_h5(게이트 ON 평범, 5폴드×5시드)"},
    "hypothesis": ("CG3 의 +0.0219 는 5시드 운값이 아니라 재현되는 효과다. 10시드로 시드를 늘렸을 때도 "
                   "대조군 대비 +0.02 이상이 유지되고 폴드별 짝 승률이 4/5 이상이면 승격 검토(dry-run)로 넘긴다."),
    "evidence": ("CG3 실측(2026-09-28 05:19, 같은 런): 게이트 ON 경로 CO_rank_smooth_d1_h5 0.5582±0.0078 "
                 "vs CO_core30_h5 0.5363±0.0188 → Δ+0.0219. 폴드별 짝: 4/5 승(0.559 vs 0.5614 한 폴드만 패). "
                 "세 방향의 단독 효과는 게이트 OFF/ON 각각 +0.0053(스무딩)·+0.0065(depth1)·+0.0100(rank×스무딩) "
                 "이었으므로 결합 +0.0219 는 부분 가산을 넘는다(상호작용 후보)."),
    "method": ("같은 패널·같은 런 2-arm(5폴드×10시드): CO_core30_h5(대조군) · CO_rank_smooth_d1_h5(실험군). "
               "성공 기준은 사전등록: Δmean ≥ +0.02 이고 폴드 짝 승률 ≥ 0.8."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 10 --only CO_core30_h5,CO_rank_smooth_d1_h5'"),
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "success": "Δ(실험군 − 같은 런 대조군) ≥ +0.02 유지 · 폴드 std ≤ 0.02 · 폴드 짝 승률 ≥ 0.8",
    "counterfactual": "CO_core30_h5 (같은 런, 게이트 ON 평범) — 프로덕션 피처 게이트 기준선",
    "est_minutes": 20,
    "cost": "100 cell (5폴드×10시드×2arm) — CG1 75cell 2.1분 실측 기준 유휴 3~6분",
    "metric": "wf_sweep_summary",
    "arm": "CO_rank_smooth_d1_h5",
    "caution": ("승격은 이 루프에서 하지 않는다 — 확인 후 champion_promote --dry-run 을 별도로 돌린다. "
                "장중 가드는 틱이 막는다."),
}
if "CG4" not in by:
    items.append(cg4)
b["updated_at"] = "2026-09-28T05:21:00+09:00"
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG3 → done(신호) · CG4 신설(priority 1) · items:", len(items))
