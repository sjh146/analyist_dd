#!/usr/bin/env python3
"""CG4 결과 기록 + CG5(150종목 전이 확인) 등록."""
import json
import shutil

P = "docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(P, P + ".bak-cg5")
b = json.load(open(P, encoding="utf-8"))
items = b["items"]
by = {i["id"]: i for i in items}

by["CG4"]["status"] = "done"
by["CG4"]["result"] = {
    "verdict": "신호 확인 — 5시드·10시드 모두 사전문턱 +0.02 초과",
    "detail": ("5폴드×10시드: CO_core30_h5 0.5365±0.0193(게이트 ON 대조군) vs "
               "CO_rank_smooth_d1_h5 0.5592±0.0075(min 0.5478 max 0.5712, 폴드승률 1.0) → Δ+0.0227. "
               "폴드별: [0.5205→0.5712][0.5092→0.5572][0.5605→0.5597][0.5529→0.5600][0.5395→0.5478] "
               "= 4/5 승, 최악 폴드도 대조군보다 +0.008 이상. 5시드 CG3(Δ+0.0219·std 0.0078)와 재현. "
               "패널 스냅샷: panel_420_asofpatch.npz(13,609행×210피처·49종목·281거래일, mtime 2026-09-25 04:02:52). "
               "라벨: 분위0.30 h5(선행 5일 수익률 상위 30%) + 스무딩(선행 1~5일 평균)."),
    "delta": 0.0227,
    "rc": 0,
}

cg5 = {
    "id": "CG5",
    "title": "우승 config(게이트 ON + rank + 스무딩 + depth1)의 150종목 패널 전이 확인",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.0,
                 "source": "같은 런 대조군 CO_core30_h5(panel_150u) — 절대값은 패널마다 달라 같은 런 비교만 유효"},
    "hypothesis": ("CG3·CG4 의 +0.022 는 49종목 패널 특성이 아니다. 150종목 패널에서도 같은 런 대조군 대비 "
                   "+0.02 가 유지되면 유니버스 의존성이 사라져 승격 근거가 강화된다."),
    "evidence": ("CG4(2026-09-28 05:22, 10시드) Δ+0.0227 · CG3(5시드) Δ+0.0219 — 둘 다 panel_420_asofpatch"
                 "(49종목)에서 측정. U1/UN1 실측에서 유니버스는 AUC 를 크게 바꾸는 축이었다"
                 "(150종목 0.5189 vs 49종목 0.5118, 같은 패널 in-run)."),
    "method": ("같은 패널(panel_150u.npz, 150종목·41,893행) 같은 런 2-arm(5폴드×5시드): CO_core30_h5 대조군 · "
               "CO_rank_smooth_d1_h5 실험군. 판정은 같은 런 델타만 — 타 패널 절대값과 비교하지 않는다."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 5400 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz "
                "--folds 5 --seeds 5 --only CO_core30_h5,CO_rank_smooth_d1_h5'"),
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "success": "같은 런 Δ ≥ +0.02 · 폴드 승률 ≥ 0.8 (미달이면 '49종목 특성' 으로 기록하고 승격 근거 약화)",
    "counterfactual": "CO_core30_h5 (같은 런·같은 패널) — 게이트 ON 평범",
    "est_minutes": 90,
    "cost": "50 cell × (49종목 cell 의 약 3배) — 유휴 15~20분, 상한 5400s",
    "metric": "wf_sweep_summary",
    "arm": "CO_rank_smooth_d1_h5",
    "caution": "장중 가드는 틱이 막는다. timeout 5400s.",
    "note": "2026-09-28 05:2x 신설. 패널 150u 의 재무 컬럼 종목별 유니크 종류 2~3개로 panel_420_asofpatch 와 구조가 유사(as-of 패치 계열) — in-run 비교라 패치 여부와 무관하게 판정 가능.",
}
if "CG5" not in by:
    items.append(cg5)
b["updated_at"] = "2026-09-28T05:26:00+09:00"
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG4 → done(신호 확인) · CG5 신설 · items:", len(items))
