#!/usr/bin/env bash
# pair_anchor_repair.sh — 짝 앵커 실측의 **소실 앵커만 재측정 + 전 앵커 재집계** (측정 정합성 수리)
#
# 왜 필요한가 (실측 2026-10-09 18:0x, CG146):
#   cg145_pair_anchor.sh 는 앵커 6개를 순차 실행하고 마지막에 전 앵커를 집계한다. 그런데 앵커 1개가
#   ① 컨테이너 재생성/SIGKILL 로 rc=137 ② DB 연결 종료(InterfaceError)로 rc=2 ('유효 폴드 없음')
#   로 실패하면 그 앵커의 산출물만 사라지고, 집계 스크립트의 `ok = all(auc ...)` 가 False 가 되어
#   `errors` 가 비지 않는다 → 구동기 `parse_protocol_pair_anchor` 가 '런 실패' → **판정불가**.
#   즉 앵커 1개 실패가 75분짜리 6앵커 실측 전체를 무효로 만든다(2026-10-09 CG146 d0: rc=137 ×4 + rc=2 ×1).
#   원 스크립트는 '전 앵커를 무조건 재실행'하므로 재집계 용도로 쓸 수 없다(같은 75분이 다시 든다).
#
# ⚠ 2026-10-09 19:0x 추가(CG148 실측): **짝의 두 arm 이 같은 모집단을 채점했는지 검사**한다.
#   실측 — CG146 d7 짝은 champ 를 rows=1750 에서(19:00:11), cand 를 rows=1780 에서(19:05:16) 채점했다.
#   5분 사이에 데이터가 30행 자랐고, 그 짝의 Δ(−0.0036)는 모델 차이가 아니라 **모집단 차이**다.
#   같은 빈티지 재실행은 비트동일(cand@d7 0.5085 ×3회: 19:05/19:20/19:25) → 이동의 원인은 비결정성이
#   아니라 데이터 증가다. 그래서 집계가 앵커별 rows_scored 를 싣고, 불일치 짝은 평균에서 제외한다.
#
# 무엇을 하는가: 산출물(${PFX}_${tag}_${arm}.json)이 **이미 있는 앵커는 건너뛰고**, 없는 앵커만 두 arm 을
#   다시 채점한 뒤 전 앵커를 재집계해 ${PFX}_pair_anchor.json 을 완성본으로 덮어쓴다.
#   집계 로직·JSON 스키마는 cg145_pair_anchor.sh 와 동일 + rows/불일치 필드 추가(구동기 파서 호환).
#
# rc: 0 = 전 앵커 완비(집계 성공) / 137 = 여전히 소실 앵커 있음(인프라 사고 → 구동기가 pending 재시도)
set -uo pipefail
REPO=/home/jhshi/analyist_dd
OUTDIR=/app/reports/overnight
HOSTOUT=$REPO/services/xgboost-ml/reports/overnight
PFX=${TAGPFX:-cg146}
LOG=$REPO/data/reports/me_cycle/logs/${PFX}_pair_anchor_repair.log
mkdir -p "$HOSTOUT" "$(dirname "$LOG")"

CFG="--universe training --label-kind rel --horizon 5 --folds 5 --dates-per-fold 10 --stocks 60"
ANCHORS="${ANCHORS:-d0:2026-10-07 d1:2026-10-06 d2:2026-10-05 d5:2026-09-29 d7:2026-09-28 d9:2026-09-25}"
ARMS="${ARMS:-champ:app/models/champion cand:app/models/champion_cand}"

echo "[$(date '+%F %T')] repair 시작 pfx=$PFX" >>"$LOG"
for kv in $ANCHORS; do
  tag=${kv%%:*}; a=${kv##*:}
  for av in $ARMS; do
    arm=${av%%:*}; dir=${av##*:}
    f="$HOSTOUT/${PFX}_${tag}_${arm}.json"
    if [ -s "$f" ]; then
      echo "    재사용 $(basename "$f")" >>"$LOG"
      continue
    fi
    echo "=== [$(date '+%F %T')] 재측정 $arm anchor=$a ($tag) ===" >>"$LOG"
    docker exec stock_xgboost_ml sh -c \
      "cd /app && OMP_NUM_THREADS=2 timeout 1200 python -u scripts/champion_robust_eval.py $CFG --model-dir $dir --asof-date $a --out $OUTDIR/${PFX}_${tag}_${arm}.json" >>"$LOG" 2>&1
    echo "    rc=$?" >>"$LOG"
  done
done

# 집계는 컨테이너 안에서(호스트 사용자는 container-root 소유 디렉터리에 못 씀 — CG143/144 실측)
docker exec -i stock_xgboost_ml python3 - "$OUTDIR" "$ANCHORS" "$ARMS" "$PFX" <<'PY' >>"$LOG" 2>&1
import json, os, statistics as st, sys
host, anchors_spec, arms_spec, pfx = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
anchors = {kv.split(":")[0]: kv.split(":")[1] for kv in anchors_spec.split()}
arms = {kv.split(":")[0]: kv.split(":")[1] for kv in arms_spec.split()}
A, B = list(arms)[0], list(arms)[1]   # 첫 arm = 대조, 둘째 arm = 가설(짝 Δ = 둘째 − 첫째)

def load(tag, arm):
    p = os.path.join(host, f"{pfx}_{tag}_{arm}.json")
    try:
        d = json.load(open(p))
        return (d.get("robust_auc"), [f.get("auc_mean") for f in (d.get("folds") or [])],
                d.get("rows_scored"), d.get("dates_scored"), None)
    except Exception as e:
        return None, [], None, None, f"{type(e).__name__}: {e}"

auc, folds, rows, dates, errs = {}, {}, {}, {}, {}
for tag in anchors:
    for arm in arms:
        a, f, r, dt, e = load(tag, arm)
        auc[(tag, arm)], folds[(tag, arm)] = a, f
        rows[(tag, arm)], dates[(tag, arm)] = r, dt
        if e:
            errs[f"{tag}/{arm}"] = e

ok = all(auc[(t, m)] is not None for t in anchors for m in arms)
thr = 0.02
out = {"metric": "protocol_pair_anchor", "anchors": anchors, "arms": arms, "pair": f"{B}-{A}",
       "config": "folds=5 dates_per_fold=10 stocks=60 h=5 rel universe=training",
       "auc": {f"{t}/{m}": auc[(t, m)] for t in anchors for m in arms},
       "folds": {f"{t}/{m}": folds[(t, m)] for t in anchors for m in arms},
       "rows_scored": {f"{t}/{m}": rows[(t, m)] for t in anchors for m in arms},
       "dates_scored": {f"{t}/{m}": dates[(t, m)] for t in anchors for m in arms},
       "pre_registered_threshold": thr, "errors": errs,
       "repaired_by": "scripts/pair_anchor_repair.sh (소실 앵커만 재측정 후 재집계)"}

if not ok:
    out["verdict"] = f"판정불가(런 실패 — 소실 앵커 재측정도 실패: {sorted(errs)})"
else:
    tags = list(anchors)
    lvl_mean = {m: round(st.fmean([auc[(t, m)] for t in tags]), 4) for m in arms}
    lvl_std = {m: round(st.pstdev([auc[(t, m)] for t in tags]), 4) for m in arms}
    deltas = {t: round(auc[(t, B)] - auc[(t, A)], 4) for t in tags}
    # 짝 유효성: 두 arm 이 같은 채점 모집단(rows)이어야 한다. 하루 안에도 데이터가 자라므로
    # 앞뒤 실행의 rows 가 다르면 Δ 는 모델 차이가 아니라 모집단 차이다(2026-10-09 CG146 d7 실측).
    bad = [t for t in tags if rows[(t, A)] != rows[(t, B)]]
    good = [t for t in tags if t not in bad]
    dvals = [deltas[t] for t in good]
    d_mean = round(st.fmean(dvals), 4) if dvals else None
    d_std = round(st.pstdev(dvals), 4) if len(dvals) > 1 else 0.0
    mark = [f"{t}:{'O' if deltas[t] >= thr else 'x'}" for t in tags]
    sig = sum(1 for t in good if deltas[t] >= thr)
    signs = {1 if deltas[t] > 0 else (-1 if deltas[t] < 0 else 0) for t in good}
    sign_consistent = (len(signs) == 1 and 0 not in signs)
    warn = (f" ⚠ 모집단 불일치(rows 다름) 짝 {bad} 는 무효로 제외 — " if bad else "")
    if not dvals:
        verdict = warn + "유효 짝 없음 — 전 앵커가 모집단 불일치(측정 프로토콜 결함)"
    elif len(set(arms.values())) == 1:
        # 두 arm 이 **같은 모델 디렉터리**면 짝 실측이 아니라 '같은 모델·같은 앵커 재실행'
        # 결정성 검정이다(CG148). '문턱 초과/미달' 로 쓰면 재현 실패(Δ≠0)가 성능 신호로 오독된다.
        worst = max(abs(v) for v in dvals)
        verdict = warn + (f"동일 모델·동일 앵커 재실행 Δ {deltas} (|최대| {worst:.4f}) — "
                          + ("결정적(재현 OK)" if worst < 0.005 else "비결정 또는 데이터 빈티지 의존(재현 실패)"))
    elif not sign_consistent:
        verdict = warn + f"부호 불일치 — 앵커별 Δ {deltas} · 단일앵커 판정은 앵커 산물"
    elif 0 < sig < len(dvals):
        verdict = warn + (f"단일앵커 판정 갈림 — {sig}/{len(dvals)} 유효앵커만 문턱 통과(문턱판정 {mark}) · "
                          f"부호는 {len(dvals)}/{len(dvals)} 일관(짝Δ 평균 {d_mean:+.4f}) → 앵커평균 짝 프로토콜 근거")
    elif sig == len(dvals):
        verdict = warn + f"전 유효앵커 문턱 초과 — 짝Δ {d_mean:+.4f}±{d_std:.4f}(부호·크기 일관)"
    else:
        verdict = warn + f"전 유효앵커 미달 — 짝Δ {d_mean:+.4f}±{d_std:.4f}(일관되게 노이즈)"
    out.update({"level_mean": lvl_mean, "level_std": lvl_std, "pair_delta": deltas,
                "pair_delta_mean": d_mean, "pair_delta_std": d_std,
                "invalid_pairs_population_mismatch": bad, "valid_anchors": good,
                "anchor_threshold_pass": mark, "anchors_over_threshold": sig,
                "sign_consistent": bool(sign_consistent), "verdict": verdict})

json.dump(out, open(os.path.join(host, f"{pfx}_pair_anchor.json"), "w"),
          ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
agg_rc=$?
echo "[$(date '+%F %T')] 집계 rc=$agg_rc" >>"$LOG"
if [ "$agg_rc" -ne 0 ]; then
  echo "[$(date '+%F %T')] DONE(집계 실패)" >>"$LOG"
  exit 137
fi
# 소실 앵커가 남았으면 인프라 사고로 보고한다(구동기가 pending 재시도).
if grep -q "판정불가(런 실패" "$HOSTOUT/${PFX}_pair_anchor.json" 2>/dev/null; then
  echo "[$(date '+%F %T')] DONE(소실 앵커 잔존)" >>"$LOG"
  exit 137
fi
echo "[$(date '+%F %T')] DONE" >>"$LOG"
exit 0
