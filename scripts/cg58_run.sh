#!/bin/sh
# cg58_run.sh — 프로덕션 승격의 WF 짝 검정 (2026-10-01 21:26 KST 승격: 0.5513 → 0.5548)
#
# 질문: 저녁 파이프라인의 승격 게이트가 통과시킨 후보(val split ensemble_auc +0.0035)가
#       우리 판정 프로토콜(같은 창·같은 유니버스 시드의 **짝 Δ**)에서도 양(+)인가?
# 근거(실측): 게이트 마진 +0.0035 는 이 스택에서 측정된 프로토콜 잡음 바닥(같은 모델·같은 시드에서
#       창 구성만 바꾼 단일 런 비교 +0.0261 · 유니버스 시드 교체 0.0211 · 같은 커맨드 반복 +0.0124,
#       CG55)보다 7배 작다 → 게이트 통과가 곧 성능 개선이라는 보증이 없다. 두 모델이 디스크에
#       함께 있으므로 재학습 없이 짝으로 잴 수 있다.
#
# 프로토콜(사전 등록)
#  - 두 arm: prev=app/models/champion_prev_20261001-122646 (09-23 모델) · new=app/models/champion (10-01 모델)
#  - 창 고정: 두 모델의 학습구간(prev 06-25~09-23 · new 07-03~10-01)을 **모두** 덮는
#    --train-start 2026-06-25 --train-end 2026-10-01 을 주어, 양쪽 모델 모두에게 OOS 인 창만 채점한다
#    (한쪽만 OOS 인 창을 섞으면 Δ 가 학습기억으로 오염된다 — CG34).
#    실측(2026-10-01, DB 200거래일 = 2025-12-01~2026-09-23, trading_dates 는 CURRENT_DATE−7 기준):
#    이 조건에서 유지되는 창은 **3개**(12-01~01-28 · 01-29~03-31 · 04-01~05-29; 06-01 이후 2창은 제외).
#  - 라벨: abs h1 **방향** — 두 모델이 실제로 학습한 과제와 동형(retrain_champion label_kind=h1_direction).
#  - 유니버스: --universe training = select_training_universe(recency, seed) — 모델과 무관하게 결정되므로
#    두 arm 이 같은 60종목을 채점한다(구성 효과 교란 0 · 실측: 같은 seed 4회 호출 시 집합 동일).
#    ⚠ seed 간 교집합은 실측 0~3종목(서로소에 가깝다) — 짝은 **같은 seed 안에서만** 성립한다.
#  - 판정은 집계기(champion_seed_family_agg)의 paired 하나뿐. 첫 arm(prev)=대조군, 둘째(new)=챌린저.
#  - 앙상블 완전성 사전 확인(실측): 두 arm 모두 pkl 3개(xgb·lgb·cat) — 부분 앙상블 채점 위험 없음.
# 한계: DB 의 마지막 거래일이 2026-09-23(=CURRENT_DATE−7 purge) 이라 남는 OOS 창은 학습구간
#    **이전**(2025-12~2026-05)뿐이다 — '암기 제거'는 보장하지만 워크포워드 **전방** 검증은 아니다.
set -e
AGG=/app/reports/cg58_summary.json
while [ $# -gt 0 ]; do
  case "$1" in
    --agg-out) AGG="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cd /app

TSTART=2026-06-25
TEND=2026-10-01
SEEDS="0 1 2 3 4"

# 같은 **날** 산출만 재사용한다 — 창(trading_dates 는 CURRENT_DATE−7 기준)과 유니버스(now−60일)가
# 실행일에 의존하므로, 날이 바뀌면 같은 파일명이라도 다른 스코어보드다. 다른 날 JSON 을 재사용하면
# 두 arm 이 서로 다른 창·유니버스로 채점된 채 짝지어져 Δ 가 조용히 오염된다(위임 리뷰 발견 C, 2026-10-01).
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

for s in $SEEDS; do
  for spec in "prev:app/models/champion_prev_20261001-122646" "new:app/models/champion"; do
    tag="${spec%%:*}"; md="${spec##*:}"
    out="app/reports/cg58_${tag}_s$s.json"
    if have_json "$out"; then
      echo "=== eval $tag seed $s — 재사용(오늘 산출) ==="
      continue
    fi
    echo "=== eval $tag seed $s ==="
    OMP_NUM_THREADS=2 timeout 900 python -u scripts/champion_robust_eval.py \
      --model-dir "$md" \
      --train-start "$TSTART" --train-end "$TEND" \
      --universe training --label-kind abs --horizon 1 \
      --folds 5 --dates-per-fold 10 --stocks 60 --universe-seed "$s" \
      --out "$out"
  done
done

echo "=== 집계(prev=대조군, new=챌린저) ==="
python scripts/champion_seed_family_agg.py --agg-out "$AGG" \
  --arm prev app/reports/cg58_prev_s0.json app/reports/cg58_prev_s1.json \
             app/reports/cg58_prev_s2.json app/reports/cg58_prev_s3.json app/reports/cg58_prev_s4.json \
  --arm new  app/reports/cg58_new_s0.json  app/reports/cg58_new_s1.json \
             app/reports/cg58_new_s2.json  app/reports/cg58_new_s3.json  app/reports/cg58_new_s4.json
echo "=== 완료: $AGG ==="
