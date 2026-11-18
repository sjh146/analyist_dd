import json, shutil

p = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(p, p + ".bak_before_cg348")
b = json.load(open(p, encoding="utf-8"))
items = b["items"] if isinstance(b, dict) and "items" in b else b

cg347 = next(i for i in items if i["id"] == "CG347")
cg347["metric"] = "robust_seed_range"
cg347["summary_out"] = "/home/jhshi/analyist_dd/data/reports/overnight/cg347_seed_range.json"
cg347["rejudge_note"] = ("2026-11-19: metric 미등록으로 rc=0 완주가 '판정불가'로 닫힘 → "
                         "scripts/robust_seed_range.py + driver 파서(robust_seed_range) 배선 후 "
                         "같은 산출물로 재판정(재실행 아님)")

cmd = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && for m in champion retrain_cg346; do "
    "for s in 0 1 2; do OMP_NUM_THREADS=4 timeout 900 python -u scripts/champion_robust_eval.py "
    "--model-dir app/models/$m --folds 5 --universe training --universe-seed $s "
    "--out /app/reports/overnight/cg348_${m}_s${s}.json || exit 1; done; done' && "
    "python3 scripts/robust_paired_seeds.py "
    "--arm-a services/xgboost-ml/reports/overnight/cg348_champion_s0.json "
    "services/xgboost-ml/reports/overnight/cg348_champion_s1.json "
    "services/xgboost-ml/reports/overnight/cg348_champion_s2.json "
    "--arm-b services/xgboost-ml/reports/overnight/cg348_retrain_cg346_s0.json "
    "services/xgboost-ml/reports/overnight/cg348_retrain_cg346_s1.json "
    "services/xgboost-ml/reports/overnight/cg348_retrain_cg346_s2.json "
    "--label-a champion --label-b retrain_cg346 "
    "--out data/reports/overnight/cg348_paired.json"
)

cg348 = {
    "id": "CG348",
    "title": ("[측정 프로토콜] 같은 유니버스 시드 짝 Δ 가 사전문턱 +0.02 를 해상하는가 — "
              "배포 챔피언 vs 재학습 후보(cg346) 3시드 짝 비교"),
    "status": "pending",
    "priority": 1,
    "est_minutes": 25,
    "owner": "model_engineer",
    "metric": "robust_paired_seeds",
    "summary_out": "/home/jhshi/analyist_dd/data/reports/overnight/cg348_paired.json",
    "command": cmd,
    "counterfactual": ("절대 AUC 비교(CG347): 같은 모델·같은 날짜에서 유니버스 seed 0/1/2 = "
                       "0.4887/0.5114/0.5011 (범위 0.0227 ≥ 문턱 0.02) → 절대 비교는 부호조차 불확실. "
                       "짝 Δ 는 이 표집 잡음을 상쇄해야 한다."),
    "success": ("① rc=0 + cg348_paired.json 생성 ② 짝 Δ SE ≤ 0.010(문턱의 1/2) 이고 "
                "③ 3/3 시드 부호 일관 → '짝 프로토콜로 +0.02 해상 가능' 기록 + 이후 arm 판정을 "
                "짝 Δ 로 전환(리뷰보드 안건). SE > 0.010 또는 부호 불일치면 시드를 5로 늘리는 "
                "축(CG349)으로 이동."),
    "expected": "측정 해상도 확보 — 20사이클 무개선 판정이 잡음인지 실재인지 가릴 프로토콜을 얻는다.",
    "created": "2026-11-19T06:20:00+09:00",
    "rationale": ("CG347 이 '단일 시드 절대 AUC 는 반폭 ±0.011 잡음'을 실측했다. 문턱 +0.02 와 "
                  "동급이므로, 짝 프로토콜이 실제로 잡음을 줄이는지 같은 데이터로 검증해야 이후 "
                  "어떤 신호도 해석할 수 있다. 판정은 여전히 다중 폴드·다중 시드만 쓴다."),
}

if not any(i["id"] == "CG348" for i in items):
    items.insert(0, cg348)

json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok items=", len(items), "| CG347 metric=", cg347["metric"], "| CG348 inserted")
