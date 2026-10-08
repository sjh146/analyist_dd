#!/usr/bin/env bash
# CG144 — 잡음바닥 **스케일링**: 앵커를 1·2·5·7거래일 옮기면 같은(불변) 챔피언의 로버스트 AUC 가
#         얼마나 흔들리는가 (2026-10-09 엔지니어 자율, CG143 후속)
#
# 왜: CG143 은 '10-07 vs 10-06' 한 쌍만 쟀다(Δ0.0175 < 문턱 0.02). 그런데 같은 계열 프로토콜로
#     같은 모델을 재측정한 13회는 0.4935~0.5448(폭 0.0513)이었다 → 이동폭이 커지면 잡음이 문턱을
#     넘는지가 '일 단위 판정 가능성'을 결정한다. 이 곡선이 없으면 문턱 변경 근거가 점 1개뿐이다.
#
# 무엇: 배포 챔피언(모델 불변)을 champion_robust_eval `--asof-date` 로 앵커만 옮겨 5회 채점.
#       집계: cg144_noise_curve.json {auc{}, deltas{} vs d0, max_abs_delta, verdict}
# 사전등록 판정: 어떤 이동폭에서 |Δ| >= 0.02 → 사전문턱은 '일 단위 판정'에 못 쓴다(프로토콜 재정의 근거).
set -uo pipefail
REPO=/home/jhshi/analyist_dd
OUTDIR=/app/reports/overnight
HOSTOUT=$REPO/services/xgboost-ml/reports/overnight
LOG=$REPO/data/reports/me_cycle/logs/cg144_noise_curve.log
mkdir -p "$HOSTOUT" "$(dirname "$LOG")"
CFG="--folds 3 --dates-per-fold 5 --stocks 60 --horizon 5 --label-kind rel --model-dir app/models/champion"
# 기준(d0) + 1·2·5·7 거래일 전 앵커 (거래일 순: 10-07, 10-06, 10-05, 09-30, 09-28)
ANCHORS="d0:2026-10-07 d1:2026-10-06 d2:2026-10-05 d5:2026-09-30 d7:2026-09-28"

for kv in $ANCHORS; do
  tag=${kv%%:*}; a=${kv##*:}
  echo "=== [$(date '+%F %T')] run $tag anchor=$a ===" >>"$LOG"
  docker exec stock_xgboost_ml sh -c \
    "cd /app && OMP_NUM_THREADS=2 timeout 900 python -u scripts/champion_robust_eval.py $CFG --asof-date $a --out $OUTDIR/cg144_$tag.json" >>"$LOG" 2>&1
  echo "    rc=$?" >>"$LOG"
done

# 집계는 **컨테이너 안**에서 한다: services/xgboost-ml/reports/overnight 는 컨테이너(root)가
# 소유해 호스트 사용자(jhshi)가 쓸 수 없다(CG143 실측). 읽기는 644 라 문제없다.
docker exec -i stock_xgboost_ml python3 - "$OUTDIR" "$ANCHORS" <<'PY' >>"$LOG" 2>&1
import json, os, sys
host, spec = sys.argv[1], sys.argv[2]
pairs = [kv.split(":") for kv in spec.split()]
anchors = {tag: a for tag, a in pairs}

def load(tag):
    try:
        return json.load(open(os.path.join(host, f"cg144_{tag}.json")))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

d = {tag: load(tag) for tag, _ in pairs}
auc = {tag: d[tag].get("robust_auc") for tag, _ in pairs}
folds = {tag: [f.get("auc_mean") for f in (d[tag].get("folds") or [])] for tag, _ in pairs}
errs = {tag: d[tag]["error"] for tag, _ in pairs if d[tag].get("error")}
base, thr = "d0", 0.02

if errs or auc.get(base) is None:
    verdict, deltas, mx = "판정불가(런 실패)", {}, None
else:
    deltas = {tag: (round(auc[tag] - auc[base], 4) if auc.get(tag) is not None else None)
              for tag, _ in pairs}
    vals = [abs(v) for k, v in deltas.items() if k != base and v is not None]
    mx = round(max(vals), 4) if vals else None
    if mx is None:
        verdict = "판정불가(런 실패)"
    elif mx >= thr:
        verdict = "잡음바닥 >= 문턱 — 앵커 이동만으로 사전문턱 도달(일 단위 판정 불가)"
    else:
        verdict = "잡음바닥 < 문턱 — 전 앵커 |Δ| < 0.02"

out = {"metric": "protocol_noise_curve", "base_anchor": anchors[base], "anchors": anchors,
       "config": "folds=3 dates_per_fold=5 stocks=60 h=5 rel model=champion",
       "auc": auc, "deltas": deltas, "folds": folds,
       "max_abs_delta": mx, "pre_registered_threshold": thr,
       "verdict": verdict, "errors": errs}
json.dump(out, open(os.path.join(host, "cg144_noise_curve.json"), "w"),
          ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
echo "DONE" >>"$LOG"
