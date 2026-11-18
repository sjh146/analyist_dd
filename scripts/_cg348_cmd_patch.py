import json, shutil

p = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
shutil.copy(p, p + ".bak_cg348cmd")
b = json.load(open(p, encoding="utf-8"))
items = b["items"]
it = next(i for i in items if i["id"] == "CG348")

it["command"] = (
    "timeout 2600 docker exec stock_xgboost_ml sh -c 'cd /app && for m in champion retrain_cg346; do "
    "for s in 0 1 2; do OMP_NUM_THREADS=4 timeout 600 python -u scripts/champion_robust_eval.py "
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
it["timeout_note"] = ("2026-11-19: 등록 자기모순 경고(timeout 900 < est 25분) 수리 — 바깥 timeout 2600"
                      "(전체 6잡) + 잡당 600(실측 165s/잡). 실측 근거: CG347 seed1 165s · seed2 166s.")
json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("patched:", it["command"][:90])
