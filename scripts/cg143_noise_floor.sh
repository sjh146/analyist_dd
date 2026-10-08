#!/usr/bin/env bash
# CG143 — 동일 모델·동일 프로토콜의 재현 잡음바닥 (2026-10-09 엔지니어 자율)
#
# 왜: 창 앵커가 CURRENT_DATE 라 하루만 지나도 표본 창·유니버스가 통째로 밀린다. 배포 챔피언은
#     2026-09-24 이후 모델이 **불변**인데 같은 계열 프로토콜 재측정 13회가 0.4935~0.5448
#     (폭 0.0513), 명시적 동일 프로토콜 1일 차 2회(CG136/139) Δ0.0227 이었다 —
#     사전문턱 +0.02 와 **같은 크기**다. 즉 지금까지의 '신호있음' 판정 다수가 잡음과 구분 불가.
#
# 무엇: 새 옵션 `--asof-date`(창 고정)로
#        ① 같은 앵커 2회 → 프로토콜 **결정성** (비트 동일해야 함)
#        ② 앵커 1일 차 1회 → **창 이동 잡음** 크기
#       를 재고 combined JSON(reports/overnight/cg143_noise_floor.json)에 기록한다.
# 사전등록 판정: 결정성 위반 → 결함(원인 규명) / 잡음 ≥ 0.02 → 사전문턱은 잡음과 구분 불가.
set -uo pipefail
REPO=/home/jhshi/analyist_dd
OUTDIR=/app/reports/overnight
HOSTOUT=$REPO/services/xgboost-ml/reports/overnight
mkdir -p "$HOSTOUT"
LOG=$REPO/data/reports/me_cycle/logs/cg143_noise_floor.log
mkdir -p "$(dirname "$LOG")"
ANCHOR_A=${ANCHOR_A:-2026-10-07}
ANCHOR_B=${ANCHOR_B:-2026-10-06}
CFG="--folds 3 --dates-per-fold 5 --stocks 60 --horizon 5 --label-kind rel --model-dir app/models/champion"

run() {  # $1 = tag, $2 = anchor
  echo "=== [$(date '+%F %T')] run $1 anchor=$2 ===" >>"$LOG"
  docker exec stock_xgboost_ml sh -c \
    "cd /app && OMP_NUM_THREADS=2 timeout 1800 python -u scripts/champion_robust_eval.py $CFG --asof-date $2 --out $OUTDIR/cg143_$1.json" \
    >>"$LOG" 2>&1
  echo "    rc=$?" >>"$LOG"
}

run a1 "$ANCHOR_A"
run a2 "$ANCHOR_A"
run b1 "$ANCHOR_B"

python3 - "$HOSTOUT" "$ANCHOR_A" "$ANCHOR_B" <<'PY' >>"$LOG" 2>&1
import json, os, sys
host, a, b = sys.argv[1], sys.argv[2], sys.argv[3]

def load(tag):
    p = os.path.join(host, f"cg143_{tag}.json")
    try:
        return json.load(open(p))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "path": p}

d = {t: load(t) for t in ("a1", "a2", "b1")}
auc = lambda x: x.get("robust_auc")
folds = lambda x: [f.get("auc_mean") for f in x.get("folds", [])]
same_det = (auc(d["a1"]) is not None and auc(d["a1"]) == auc(d["a2"])
            and folds(d["a1"]) == folds(d["a2"]))
vals = [v for v in (auc(d["a1"]), auc(d["b1"])) if v is not None]
shift = round(abs(vals[0] - vals[1]), 4) if len(vals) == 2 else None
verdict = ("결정성 위반 — 프로토콜 비결정성 결함(원인 규명 필요)" if not same_det else
           "잡음바닥 >= 문턱 — 사전문턱 +0.02 는 잡음과 구분 불가" if (shift is not None and shift >= 0.02) else
           "잡음바닥 < 문턱" if shift is not None else
           "판정불가(런 실패)")
out = {
    "metric": "protocol_noise_floor",
    "anchors": {"A": a, "B": b},
    "config": "folds=3 dates_per_fold=5 stocks=60 h=5 rel model=champion",
    "determinism_same_anchor": bool(same_det),
    "auc": {"a1": auc(d["a1"]), "a2": auc(d["a2"]), "b1": auc(d["b1"])},
    "anchor_shift_delta": shift,
    "folds": {"a1": folds(d["a1"]), "b1": folds(d["b1"])},
    "windows": {"a1": [f.get("window") for f in d["a1"].get("folds", [])],
                "b1": [f.get("window") for f in d["b1"].get("folds", [])]},
    "pre_registered_threshold": 0.02,
    "verdict": verdict,
    "errors": {t: d[t].get("error") for t in d if d[t].get("error")},
}
json.dump(out, open(os.path.join(host, "cg143_noise_floor.json"), "w"),
          ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
echo "DONE" >>"$LOG"
