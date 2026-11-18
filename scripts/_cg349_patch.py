import json, shutil, datetime

p = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(p, p + ".bak_cg349")
b = json.load(open(p, encoding="utf-8"))
items = b["items"]

# ── CG348 정정: '노이즈'가 아니라 측정무효(창 불일치) ─────────────────────────
cg348 = next(i for i in items if i["id"] == "CG348")
cg348["status"] = "done"
cg348["result"] = {
    "verdict": "측정무효 — arm 간 채점 창 불일치(Δ 무의미)",
    "detail": ("챔피언 train_end 2026-10-01(메타) → OOS 창 2026-01-16~05-08(2폴드) vs "
               "후보 cg346 train_end 2026-08-27 → OOS 창 2026-09-11~11-04(1폴드). "
               "짝 Δ [s0 +0.0508, s1 -0.0288, s2 +0.0086] 평균 +0.0102·SE 0.0230 은 "
               "서로 다른 날짜 구간을 뺀 값이라 판정 근거가 아니다. "
               "계측기 수리: robust_paired_seeds.py 가 window_mismatch/fold_mismatch 를 계산해 "
               "불일치면 meets_threshold=false + 판정기(judge_seed_family)가 '판정불가'로 막는다. "
               "부수 실측: 절대 AUC 시드 변동 — 챔피언 0.5383/0.4826/0.5097(범위 0.0557!), "
               "후보 0.4875/0.5114/0.5011(범위 0.0239, s1·s2 는 CG347 과 소수점 일치 → 결정적)."),
    "rc": 0,
    "delta": None,
}
cg348["followup"] = "CG349 — 같은 train_end 로 창을 고정한 재측정"

# ── CG349: 창 고정 짝 재측정 ────────────────────────────────────────────────
cmd = (
    "timeout 2600 docker exec stock_xgboost_ml sh -c 'cd /app && for m in champion retrain_cg346; do "
    "for s in 0 1 2; do OMP_NUM_THREADS=4 timeout 600 python -u scripts/champion_robust_eval.py "
    "--model-dir app/models/$m --folds 5 --dates-per-fold 10 "
    "--train-start 2025-09-24 --train-end 2026-10-01 "
    "--universe training --universe-seed $s "
    "--out /app/reports/overnight/cg349_${m}_s${s}.json || exit 1; done; done' && "
    "python3 scripts/robust_paired_seeds.py "
    "--arm-a services/xgboost-ml/reports/overnight/cg349_champion_s0.json "
    "services/xgboost-ml/reports/overnight/cg349_champion_s1.json "
    "services/xgboost-ml/reports/overnight/cg349_champion_s2.json "
    "--arm-b services/xgboost-ml/reports/overnight/cg349_retrain_cg346_s0.json "
    "services/xgboost-ml/reports/overnight/cg349_retrain_cg346_s1.json "
    "services/xgboost-ml/reports/overnight/cg349_retrain_cg346_s2.json "
    "--label-a champion --label-b retrain_cg346 "
    "--out data/reports/overnight/cg349_paired.json"
)

cg349 = {
    "id": "CG349",
    "title": ("[측정 프로토콜] 채점 창을 train_end=2026-10-01 로 고정한 3시드 짝 Δ — "
              "배포 챔피언 vs 재학습 후보(cg346) (CG348 창 불일치 수리 후 재측정)"),
    "status": "pending",
    "priority": 1,
    "est_minutes": 25,
    "owner": "model_engineer",
    "metric": "robust_paired_seeds",
    "summary_out": "/home/jhshi/analyist_dd/data/reports/overnight/cg349_paired.json",
    "command": cmd,
    "counterfactual": ("CG348: 창을 고정하지 않아 챔피언(2026-01~05·2폴드)과 후보(2026-09~11·1폴드)를 "
                       "다른 구간에서 채점 → Δ +0.0102·SE 0.0230 은 무의미. 창을 맞추면 두 arm 이 "
                       "같은 10날짜에서 채점되어 시드 표집 잡음이 짝 안에서 상쇄된다."),
    "success": ("① rc=0 + cg349_paired.json ② window_mismatch·fold_mismatch 가 빈 리스트(창 일치 확인) "
                "③ 짝 Δ SE ≤ 0.010 이면 '짝 프로토콜로 +0.02 해상 가능' 기록·리뷰보드 안건 상정. "
                "SE > 0.010 이면 시드 5개로 확장(CG350). 어느 경우든 Δ 부호로 승격 판단 금지."),
    "expected": "해상 가능한 측정 프로토콜 확보 — 20사이클 무개선이 잡음인지 실재인지 가른다.",
    "created": "2026-11-19T06:55:00+09:00",
    "rationale": ("CG347 이 절대 AUC 시드 범위 0.0227(문턱 1.14배)을, CG348 이 '창이 다르면 짝도 "
                  "무의미하다'를 실측했다. 두 교훈을 합치면 창을 고정한 같은 시드 짝만이 문턱을 "
                  "해상할 후보 프로토콜이다."),
}
if not any(i["id"] == "CG349" for i in items):
    items.insert(0, cg349)

json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ── 원장 정정(CG348 행에 corrected_verdict 부기) ────────────────────────────
lp = "/home/jhshi/analyist_dd/data/reports/model_engineer_ledger.jsonl"
shutil.copy(lp, lp + ".bak_cg348")
rows = [json.loads(l) for l in open(lp, encoding="utf-8") if l.strip()]
n = 0
for r in rows:
    if r.get("id") == "CG348" and r.get("verdict") == "노이즈":
        r["corrected_verdict"] = "측정무효 — arm 간 채점 창 불일치(Δ 무의미)"
        r["corrected_note"] = ("원 판정 '노이즈'는 창 불일치를 반영하지 못했다. 챔피언 2폴드"
                               "(2026-01~05) vs 후보 1폴드(2026-09~11). 계측기 수리로 재발 차단.")
        n += 1
with open(lp, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print("backlog ok | ledger corrected rows:", n)
