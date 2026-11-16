#!/usr/bin/env bash
# CG320 — 널 편향 +0.011 의 원인 분해.
# ⚠ 실측(2026-11-17 07:2x~07:5x, 4코어·부하 3~4): 5폴드 quant arm 은 50초인데
#   10폴드 arm 과 relative 라벨 arm 은 같은 패널에서 900초를 넘겼다(arm 당 런타임 편차 20배).
#   → arm 별 timeout 을 2700초로 올린다(900초로는 두 arm 이 rc=124 로 소실).
# ⚠ 수리(2026-11-17 07:5x): run() 이 "$@" 를 docker exec 문자열에 붙이지 않아 --only 가 무시됐고
#   전 config 스윕(100+ arm)이 돌아 3건이 rc=124 로 소실됐다. 이제 "$*" 로 전달한다.
# 전제: CG319 산출물 panel_420_asofpatch_nullperm.npz (피처만 날짜내 교란된 패널).
# A 폴드 폭·겹침 : --folds 10 (테스트 창 23일) vs 5(46일)
# B 라벨 정의    : LS_rel_h5 (날짜내 중앙값 상대) vs LS_quant_q30_h5 (꼬리 30/30)
# C 선별 유무    : CO_core_all_h5(48 전량) vs top30
# D 학습/검증    : --report-train-auc 로 in-sample AUC 를 함께 남긴다(오버샘플 중복 진단)
set -u
cd /home/jhshi/analyist_dd || exit 1
NULL=/app/app/models/wf/panel_420_asofpatch_nullperm.npz
OUT=/app/reports/overnight

run () {  # run <tag> <extra args...>
  local tag="$1"; shift
  docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=3 timeout 2700 python -u scripts/wf_label_sweep.py \
    --panel $NULL --out $OUT/cg320_${tag}.jsonl --summary-out $OUT/cg320_${tag}.json $*" >/dev/null 2>&1
  echo "$tag rc=$?"
}

run A_f10   --folds 10 --seeds 3 --only LS_quant_q30_h5
run B_rel   --folds 5  --seeds 3 --only LS_rel_h5
run C_all   --folds 5  --seeds 3 --only CO_core_all_h5
run D_train --folds 5  --seeds 3 --only LS_quant_q30_h5 --report-train-auc

docker exec stock_xgboost_ml python - <<'PY'
import glob, json, os
OUT = "/app/reports/overnight"
rows = {}
for p in sorted(glob.glob(os.path.join(OUT, "cg320_*.json"))):
    tag = os.path.basename(p)[len("cg320_"):-5]
    d = json.load(open(p))
    b = d.get("best") or {}
    fl = b.get("folds") or {}
    ks = sorted(fl)
    fm = [fl[k]["mean"] for k in ks]
    tr = [fl[k].get("train_auc") for k in ks]
    rows[tag] = {
        "exp": b.get("exp"),
        "folds": [round(x, 4) for x in fm],
        "mean": round(sum(fm) / len(fm), 4) if fm else None,
        "n_folds": len(ks),
        "train_auc": [round(x, 4) if isinstance(x, (int, float)) else None for x in tr],
        "pooled": round(sum(fl[k]["ens_pooled_auc"] for k in ks) / len(ks), 4) if ks else None,
        "daily": round(sum(fl[k]["daily_auc_mean"] for k in ks) / len(ks), 4) if ks else None,
    }
json.dump({"null_baseline_cg319": 0.5113, "arms": rows},
          open(os.path.join(OUT, "cg320_null_bisect.json"), "w"), indent=1)
print(json.dumps(rows, indent=1))
PY
