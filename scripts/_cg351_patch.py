import json, shutil

p = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(p, p + ".bak_cg351")
b = json.load(open(p, encoding="utf-8"))
items = b["items"]

cmd = (
    "timeout 2600 docker exec stock_xgboost_ml sh -c 'cd /app && for s in 3 4; do "
    "OMP_NUM_THREADS=4 timeout 600 python -u scripts/champion_robust_eval.py "
    "--model-dir app/models/retrain_cg346 --folds 5 --universe training --universe-seed $s "
    "--out /app/reports/overnight/cg351_seed$s.json || exit 1; done' && "
    "python3 scripts/robust_seed_range.py --inputs "
    "services/xgboost-ml/reports/overnight/cg346c_robust_trainuniv.json "
    "services/xgboost-ml/reports/overnight/cg347_seed1.json "
    "services/xgboost-ml/reports/overnight/cg347_seed2.json "
    "services/xgboost-ml/reports/overnight/cg351_seed3.json "
    "services/xgboost-ml/reports/overnight/cg351_seed4.json "
    "--out data/reports/overnight/cg351_seed_range5.json"
)

it = {
    "id": "CG351",
    "title": ("[측정 잡음] 유니버스 시드 잡음 밴드를 5시드로 확장 — CG347(3시드 범위 0.0227)의 σ 추정을 df=4 로 재산출"),
    "status": "pending",
    "priority": 2,
    "est_minutes": 14,
    "owner": "model_engineer",
    "metric": "robust_seed_range",
    "summary_out": "/home/jhshi/analyist_dd/data/reports/overnight/cg351_seed_range5.json",
    "command": cmd,
    "counterfactual": ("CG347 3시드: 0.4887/0.5114/0.5011 → 범위 0.0227 · std 0.0114 (df=2, σ 추정 불안정). "
                       "같은 모델·같은 10날짜에서 시드만 3/4 추가."),
    "success": ("① rc=0 ② 5시드 σ 와 범위 산출 ③ 범위 ≥ 0.02 유지면 '단일 시드 절대 비교 반폭'을 "
                "σ 기반(±1.4σ)으로 확정 보고. 범위가 0.02 미만으로 내려가면 3시드 우연이었으므로 "
                "그 사실을 함께 기록(어느 쪽이든 프로토콜 변경 근거는 리뷰보드)."),
    "expected": "스코어보드 기준선 비교에 붙일 오차 막대(반폭)의 신뢰도 확보.",
    "created": "2026-11-19T06:58:00+09:00",
    "rationale": ("CG347 의 σ=0.0114 는 3점 추정이라 신뢰구간이 넓다. 리뷰보드에 '기준선 비교 반폭'을 "
                  "올리려면 시드 5개가 필요하다(2잡 추가 = 약 6분, 유휴 CPU)."),
}
if not any(i["id"] == "CG351" for i in items):
    items.insert(0, it)
json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG351 added:", it["status"])
