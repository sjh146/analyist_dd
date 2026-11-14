#!/usr/bin/env python3
"""CG276(지평 사다리) 백로그 등록 — 2026-11-14, CG275 실측 후속."""
import json
import os

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
b = json.load(open(P, encoding="utf-8"))
if any(i["id"] == "CG276" for i in b["items"]):
    print("이미 있음")
    raise SystemExit(0)
b["items"].append({
    "id": "CG276",
    "status": "pending",
    "priority": 1,
    "kind": "model",
    "owner": "engineer",
    "affects_model": True,
    "metric": "horizon_ladder",
    "title": ("[모델 축·지평 사다리] 정보 상한은 지평(h5)의 성질인가 피처의 성질인가 — 같은 피처·같은 "
              "저용량에서 라벨 지평만 h1/h3/h5/h10 으로 바꿔 OOS AUC 를 비교"),
    "hypothesis": (
        "CG275 실측(2026-11-14, 5폴드×3시드): 기본 용량 arm CO_core30_h5 train 0.7602 vs OOS 0.5323(갭 0.228) · "
        "저용량 arm CO_smooth_d1_h5 train 0.5870 vs OOS 0.5352(갭 0.052). 용량을 줄이면 갭이 4.4배 줄지만 "
        "OOS 는 오히려 +0.0029 좋아졌다 → 갭은 '외운 것'이고, **일반화되는 신호는 ~0.535 수준**이다. "
        "즉 용량·정규화 투자로 얻을 것이 없다. 남은 질문: 그 0.535 가 **h5 라벨의 성질**인가(더 짧은 지평이면 "
        "정보가 더 많다) 아니면 **피처 집합의 성질**인가(지평을 바꿔도 0.53). 1폴드 스모크에서 h1 0.5469 · "
        "h3 0.5434 로 h5(0.5352)보다 높게 나와 이 축을 정식으로 재야 한다."),
    "counterfactual": "CO_smooth_d1_h5 (같은 패널·같은 폴드·같은 시드·같은 저용량에서 지평 h5)",
    "command": "timeout 3000 bash scripts/cg276_horizon_ladder.sh",
    "summary_out": "/app/reports/overnight/cg276_horizon_ladder.json",
    "paired_reference": "CO_smooth_d1_h5",
    "success": ("기준 arm(CO_smooth_d1_h5) 대비 어떤 지평의 OOS 폴드평균이 Δ ≥ +0.02 이고 그 arm 의 "
                "폴드승률 ≥ 4/5 → 신호(그 지평으로 라벨·홀드 축 실험 등록). 미달이면 '지평 축 종결'. "
                "⚠ AUC 는 정보량 비교이며 돈 지표 대체가 아니다(지평별 거래비용·회전율 상이) — "
                "신호가 나와도 순기대(%p/세션, 체결성 필터+수수료 0.21%p)로 별도 확인한다."),
    "expected": "h1 0.540~0.550 · h3 0.535~0.545 · h5 0.530~0.540 · h10 0.520~0.535 (짧을수록 높을 것으로 추정)",
    "cost": "낮음(4 arm × 5폴드 × 3시드 · 유휴 시 15~25분)",
    "est_minutes": 20,
    "created_at": "2026-11-14T17:17:00+09:00",
})
b["updated_at"] = "2026-11-14T17:17:00+09:00"
tmp = P + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
os.replace(tmp, P)
print("등록 완료 CG276 pending")
