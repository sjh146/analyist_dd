#!/usr/bin/env python3
"""CG277 등록 — 지평 사다리의 **돈 지표** 짝(체결성 필터 + 수수료 0.21%p) — 2026-11-14."""
import json
import os

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
b = json.load(open(P, encoding="utf-8"))
if any(i["id"] == "CG277" for i in b["items"]):
    print("이미 있음")
    raise SystemExit(0)
b["items"].append({
    "id": "CG277",
    "status": "needs_setup",
    "priority": 1,
    "kind": "money",
    "owner": "engineer",
    "affects_model": False,
    "metric": "fillable_topk_vs_pool",
    "title": ("[돈 지표·지평] CG276 지평 사다리(h1>h3>h5>h10)를 **돈으로** 재측정 — 체결성 필터 + "
              "수수료 왕복 0.21%p 반영 top-k 초과기대"),
    "hypothesis": (
        "CG276 실측(5폴드×3시드, panel_prod200, 저용량 depth1): OOS AUC h1 0.5442±0.0065 > h3 0.5401 "
        "> h5 0.5352 > h10 0.5325 로 **완전 단조**이고, 갭도 0.018/0.034/0.052/0.086 으로 같이 줄어든다. "
        "h1−h5 Δ+0.0090 은 사전문턱 +0.02 미달이라 AUC 로는 '노이즈'지만, 짧은 지평은 회전율이 높아 "
        "**수수료 0.21%p 왕복이 더 자주 붙는다** → AUC 가 조금 높은 것이 돈으로는 역전될 수 있다. "
        "또 CG275 로 용량 축(과적합)은 기각됐으므로, 지평 축을 AUC 만으로 닫으면 '돈으로는 살아 있는 축'을 "
        "근거 없이 버리게 된다(스킬 최우선 규칙: 축 종결도 돈 지표로 남긴다)."),
    "counterfactual": "같은 dump·같은 유니버스의 **세션 풀평균**(널 기준선, k 무관)",
    "success": (
        "① h1·h5 두 arm 의 top-k 초과기대(체결성 필터 ON · 수수료 0.21%p 차감)를 같은 dump 에서 산출 "
        "② 호라이즌 표(h1/h3/h5/h10)에서 초과기대가 +0.1%p/세션 이상이고 앞/뒤 절반 부호가 같으면 "
        "'지평 축 생존'(그 지평으로 후속 실험) ③ 미달이면 지평 축을 **돈 근거로** 종결"),
    "command": None,
    "setup_needed": (
        "배선 필요(엔지니어 자율, 다음 틱): ① `wf_label_sweep.py --dump-preds` 로 CG276_h1·CO_smooth_d1_h5 "
        "두 arm 의 폴드 예측을 덤프(같은 폴드·같은 시드·같은 패널) ② `scripts/fillable_topk_horizon_sweep.py` "
        "(--arm-jsonl / --control-jsonl / --k 3,5,10,20,30 / --horizons 1,3,5,10 / --primary 1)로 초과기대 계산 "
        "③ metric 을 `fillable_topk_vs_pool` 로 등록해 기존 판정기를 그대로 쓴다(새 판정기 금지). "
        "선행 확인: 덤프 스키마(date·code·y_pred·fwd_ret)가 fillable 기대값 계산기에 그대로 먹는지 1회 검증 — "
        "안 먹으면 컬럼 매핑만 고친다(CG246 패턴 재사용)."),
    "expected": "h1 초과기대가 수수료 후 +0.1%p 미만(회전율 부담) — 즉 'AUC 우위가 돈으로 이어지지 않음' 쪽",
    "cost": "중간(덤프 2 arm × 5폴드 × 3시드 ≈ 10분 + 집계 수 분)",
    "est_minutes": 25,
    "created_at": "2026-11-14T17:30:00+09:00",
})
b["updated_at"] = "2026-11-14T17:30:00+09:00"
tmp = P + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
os.replace(tmp, P)
print("등록 완료 CG277 needs_setup")
