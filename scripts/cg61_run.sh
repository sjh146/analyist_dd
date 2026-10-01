#!/bin/sh
# cg61_run.sh — 게이트 승격 판정의 10시드 확대 (CG58 5시드 재측정)
#
# 질문: CG58(5시드) 짝 Δ -0.0136(SE 0.0099 · t -1.38)이 '노이즈'인가 '유의한 하락'인가?
# 근거: 승격 게이트 개편(기준선·바닥값)은 사람 승인 대상이고, 그 근거의 강도는
#       '게이트가 승격시킨 모델이 우리 프로토콜에서 개선이 아니다'의 통계에 달려 있다.
#       시드 5→10 이면 SE 0.0099 → 약 0.0052, Δ=-0.014 면 t≈-2.6 (사전문턱 +0.02 = 3.8σ 검출).
#
# 프로토콜(사전 등록 — CG58 과 비트 동일, 시드 수만 확대)
#  - arm prev = app/models/champion_prev_20261001-122646 (09-23 학습분)
#    arm new  = app/models/champion                    (10-01 학습분, 게이트가 승격)
#  - 창 고정 --train-start 2026-06-25 --train-end 2026-10-01 (두 학습구간을 모두 덮어 양쪽 다 OOS)
#  - 라벨 abs h1 (두 모델의 실제 학습 과제) · --universe training(시드 0..9, 모델 무관 결정)
#  - 판정은 집계기 paired 하나뿐. 첫 arm(prev)=대조군.
# 한계: 남는 OOS 창은 학습구간 **이전**(2025-12~2026-05)뿐 — 전방 워크포워드가 아니다.
set -e
AGG=/app/reports/cg61_summary.json
while [ $# -gt 0 ]; do
  case "$1" in
    --agg-out) AGG="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cd /app

TSTART=2026-06-25
TEND=2026-10-01
SEEDS="0 1 2 3 4 5 6 7 8 9"

# 같은 **날** 산출만 재사용(창·유니버스가 실행일에 의존 — 다른 날 JSON 재사용은 Δ 오염).
have_json() {
  [ -s "$1" ] || return 1
  python -c 'import json,sys,datetime
try:
    d = json.load(open(sys.argv[1]))
    when = datetime.datetime.fromisoformat(d.get("measured_at") or "").date()
except Exception:
    sys.exit(1)
sys.exit(0 if when == datetime.datetime.utcnow().date() else 1)' "$1"
}

ARGS=""
for s in $SEEDS; do
  for spec in "prev:app/models/champion_prev_20261001-122646" "new:app/models/champion"; do
    tag="${spec%%:*}"; md="${spec##*:}"
    out="app/reports/cg61_${tag}_s$s.json"
    if have_json "$out"; then
      echo "=== eval $tag seed $s — 재사용(오늘 산출) ==="
    else
      echo "=== eval $tag seed $s ==="
      OMP_NUM_THREADS=2 timeout 900 python -u scripts/champion_robust_eval.py \
        --model-dir "$md" \
        --train-start "$TSTART" --train-end "$TEND" \
        --universe training --label-kind abs --horizon 1 \
        --folds 5 --dates-per-fold 10 --stocks 60 --universe-seed "$s" \
        --out "$out"
    fi
    ARGS="$ARGS app/reports/cg61_${tag}_s$s.json"
  done
done

echo "=== 집계(prev=대조군, new=챌린저 · 기대 시드 10) ==="
# 셸 변수로 --out 을 만들지 않는다(구동기가 커맨드 문자열에서 첫 --out 만 읽는다 — CG55 함정).
# arm 인자는 아래에 리터럴로 전개한다.
python scripts/champion_seed_family_agg.py --agg-out "$AGG" --expect-seeds 10 \
  --arm prev app/reports/cg61_prev_s0.json app/reports/cg61_prev_s1.json \
             app/reports/cg61_prev_s2.json app/reports/cg61_prev_s3.json \
             app/reports/cg61_prev_s4.json app/reports/cg61_prev_s5.json \
             app/reports/cg61_prev_s6.json app/reports/cg61_prev_s7.json \
             app/reports/cg61_prev_s8.json app/reports/cg61_prev_s9.json \
  --arm new  app/reports/cg61_new_s0.json  app/reports/cg61_new_s1.json \
             app/reports/cg61_new_s2.json  app/reports/cg61_new_s3.json \
             app/reports/cg61_new_s4.json  app/reports/cg61_new_s5.json \
             app/reports/cg61_new_s6.json  app/reports/cg61_new_s7.json \
             app/reports/cg61_new_s8.json  app/reports/cg61_new_s9.json
echo "=== 완료: $AGG ==="
