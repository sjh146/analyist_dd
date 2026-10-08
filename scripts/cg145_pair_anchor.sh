#!/usr/bin/env bash
# CG145 — 앵커 평균 프로토콜 실측: **짝 Δ(두 arm 을 같은 앵커에서 함께 측정)** 가
#         레벨(단일 arm 의 앵커별 AUC)보다 안정적인가 (2026-10-09 엔지니어 자율, CG144 후속)
#
# 왜: CG144 실측 — 모델·config·데이터를 모두 고정하고 평가 앵커만 0/1/2/5/7거래일 옮기면
#     같은 모델의 로버스트 AUC 가 0.4531→0.5128 (최대 |Δ| 0.0597 = 사전문턱 0.02 의 2.99배)로
#     요동친다. 즉 '단일 실행 Δ >= +0.02' 를 신호로 부르는 현 문턱은 앵커 잡음과 구분 불가다.
#     남은 질문: 두 arm 을 **같은 앵커에서 짝으로** 재면 앵커 공통분이 상쇄돼 Δ 의 std 가
#     레벨의 std 보다 작아지는가? 작아지면 '복수 고정 앵커 평균 짝 Δ' 로 판정 문턱을 다시 세울 수 있다.
#
# 무엇: 배포 챔피언(champion·173피처)과 10-08 저녁 챌린저(champion_cand·199피처·val AUC 0.6308)를
#       같은 앵커 3개(2026-10-07/10-05/09-28)에서 CG139/CG140 과 같은 config 로 각각 채점한다.
#       집계: 레벨 std(champion, cand 각각) vs 짝 Δ std · Δ 부호 일관성.
# 판정(사전등록): 짝 Δ std < 레벨 std 이고 Δ 부호가 3앵커에서 일관되면 → '앵커평균 짝 프로토콜' 채택 근거.
#                 부호가 뒤섞이면 → CG139(+0.0290)·CG140(+0.0056) 류 판정 자체가 앵커 산물.
# ⚠ 이 항목은 판정 **절차**를 바꾸는 실측이다 — 문턱·기준선 변경은 리뷰보드 승인 대상(CG145 본항목).
set -uo pipefail
REPO=/home/jhshi/analyist_dd
OUTDIR=/app/reports/overnight
HOSTOUT=$REPO/services/xgboost-ml/reports/overnight
LOG=$REPO/data/reports/me_cycle/logs/${TAGPFX:-cg145}_pair_anchor.log
mkdir -p "$HOSTOUT" "$(dirname "$LOG")"

CFG="--universe training --label-kind rel --horizon 5 --folds 5 --dates-per-fold 10 --stocks 60"
# 앵커·arm 은 env 로 확장 가능(기본 = CG145 3앵커 실측값). --asof-date 는 '≤ 앵커−7일' 의 최근
# 거래일부터 창을 잡으므로, 앵커는 DB 거래일 기준으로 서로 다른 마지막날을 주도록 고른다.
# 실측 매핑(2026-10-09): 10-07→09-30 · 10-06→09-29 · 10-05→09-28 · 09-29→09-22 · 09-28→09-21 · 09-25→09-18
ANCHORS="${ANCHORS:-d0:2026-10-07 d2:2026-10-05 d7:2026-09-28}"
ARMS="${ARMS:-champ:app/models/champion cand:app/models/champion_cand}"
TAGPFX="${TAGPFX:-cg145}"

for kv in $ANCHORS; do
  tag=${kv%%:*}; a=${kv##*:}
  for av in $ARMS; do
    arm=${av%%:*}; dir=${av##*:}
    echo "=== [$(date '+%F %T')] run $arm anchor=$a ($tag) ===" >>"$LOG"
    docker exec stock_xgboost_ml sh -c \
      "cd /app && OMP_NUM_THREADS=2 timeout 1200 python -u scripts/champion_robust_eval.py $CFG --model-dir $dir --asof-date $a --out $OUTDIR/${TAGPFX}_${tag}_${arm}.json" >>"$LOG" 2>&1
    echo "    rc=$?" >>"$LOG"
  done
done

# 집계는 컨테이너 안에서(호스트 사용자는 container-root 소유 디렉터리에 못 씀 — CG143/144 실측)
docker exec -i stock_xgboost_ml python3 - "$OUTDIR" "$ANCHORS" "$ARMS" "$TAGPFX" <<'PY' >>"$LOG" 2>&1
import json, os, statistics as st, sys
host, anchors_spec, arms_spec, pfx = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
anchors = {kv.split(":")[0]: kv.split(":")[1] for kv in anchors_spec.split()}
arms = {kv.split(":")[0]: kv.split(":")[1] for kv in arms_spec.split()}
A, B = list(arms)[0], list(arms)[1]   # 첫 arm = 대조, 둘째 arm = 가설(짝 Δ = 둘째 − 첫째)

def load(tag, arm):
    p = os.path.join(host, f"{pfx}_{tag}_{arm}.json")
    try:
        d = json.load(open(p))
        return d.get("robust_auc"), [f.get("auc_mean") for f in (d.get("folds") or [])], None
    except Exception as e:
        return None, [], f"{type(e).__name__}: {e}"

auc, folds, errs = {}, {}, {}
for tag in anchors:
    for arm in arms:
        a, f, e = load(tag, arm)
        auc[(tag, arm)], folds[(tag, arm)] = a, f
        if e:
            errs[f"{tag}/{arm}"] = e

ok = all(auc[(t, m)] is not None for t in anchors for m in arms)
thr = 0.02   # 사전등록 문턱 — dict 리터럴보다 먼저 바인딩(2026-10-09 NameError 실측)
out = {"metric": "protocol_pair_anchor", "anchors": anchors, "arms": arms, "pair": f"{B}-{A}",
       "config": "folds=5 dates_per_fold=10 stocks=60 h=5 rel universe=training",
       "auc": {f"{t}/{m}": auc[(t, m)] for t in anchors for m in arms},
       "folds": {f"{t}/{m}": folds[(t, m)] for t in anchors for m in arms},
       "pre_registered_threshold": thr, "errors": errs}

if not ok:
    out["verdict"] = "판정불가(런 실패 — 로그 확인)"
else:
    tags = list(anchors)
    lvl_mean = {m: round(st.fmean([auc[(t, m)] for t in tags]), 4) for m in arms}
    lvl_std = {m: round(st.pstdev([auc[(t, m)] for t in tags]), 4) for m in arms}
    deltas = {t: round(auc[(t, B)] - auc[(t, A)], 4) for t in tags}
    dvals = [deltas[t] for t in tags]
    d_mean = round(st.fmean(dvals), 4)
    d_std = round(st.pstdev(dvals), 4) if len(dvals) > 1 else 0.0
    mark = [f"{t}:{'O' if deltas[t] >= thr else 'x'}" for t in tags]
    sig = sum(1 for v in dvals if v >= thr)
    signs = {1 if v > 0 else (-1 if v < 0 else 0) for v in dvals}
    sign_consistent = (len(signs) == 1 and 0 not in signs)
    if not sign_consistent:
        verdict = f"부호 불일치 — 앵커별 Δ {deltas} · 단일앵커 판정은 앵커 산물"
    elif 0 < sig < len(dvals):
        verdict = (f"단일앵커 판정 갈림 — {sig}/{len(dvals)} 앵커만 문턱 통과(문턱판정 {mark}) · "
                   f"부호는 {len(dvals)}/{len(dvals)} 일관(짝Δ 평균 {d_mean:+.4f}) → 앵커평균 짝 프로토콜 필요 근거 확보")
    elif sig == len(dvals):
        verdict = f"전 앵커 문턱 초과 — 짝Δ {d_mean:+.4f}±{d_std:.4f}(부호·크기 일관)"
    else:
        verdict = f"전 앵커 미달 — 짝Δ {d_mean:+.4f}±{d_std:.4f}(일관되게 노이즈)"
    out.update({"level_mean": lvl_mean, "level_std": lvl_std, "pair_delta": deltas,
                "pair_delta_mean": d_mean, "pair_delta_std": d_std,
                "anchor_threshold_pass": mark, "anchors_over_threshold": sig,
                "sign_consistent": bool(sign_consistent), "verdict": verdict})

json.dump(out, open(os.path.join(host, f"{pfx}_pair_anchor.json"), "w"),
          ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
echo "DONE" >>"$LOG"
