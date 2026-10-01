#!/bin/sh
# cg57_run.sh — CG57 학습 유니버스 정렬 A/B (recency vs liquidity), 같은 창·같은 라벨·같은 짝 프로토콜.
#
# 질문: 현행 생산 학습 표본(select_training_universe, 최신일+시드 셔플 = 실측상 **무작위 표본**)을
#       일평균 거래대금 상위 200종목으로 바꾸면 견고 AUC 가 오르는가?
# 근거: 현행 표본과 유동성 표본의 교집합은 실측 14/200(중앙 거래대금 3.9억 vs 473.6억) — 거의 서로소다.
#       즉 '무엇을 학습하는가'가 크게 달라지는데, 이 축은 아직 측정된 적이 없다.
#
# 프로토콜(사전 등록)
#  - 두 arm 은 **유니버스 모드만** 다르다: 같은 라벨(rel h5)·같은 구간(90일)·같은 HP·같은 코드.
#  - 학습 구간은 창이 실행 시각에 붙지 않게 --end-date 로 고정한다(체크포인트 키 유지 + A/B 성립).
#  - 평가는 champion_robust_eval --universe training --universe-seed {0..4} — 이 유니버스 함수는
#    **모델과 무관**하게 결정되므로 두 arm 이 같은 60종목을 채점한다(구성 효과 교란 없음).
#  - --train-start/--train-end 로 학습구간과 겹치는 창은 제외한다(CG34 교훈: 겹치면 AUC 가 부푼다).
#  - 판정은 집계기(champion_seed_family_agg)의 `paired` 하나뿐이다 — 첫 arm(rec)=대조군, 둘째(liq)=챌린저.
#
# 재개: ①학습은 retrain_champion 체크포인트(500페어) + pkl 3종 존재 시 건너뜀 ②평가는 JSON 파싱되면 건너뜀.
# 왜 재개가 필수인가(실측 2026-10-01 CG56): 바깥 timeout 이 몇 분 모자라면 그 실행은 통째로 버려지고
# 재시도 비용이 매번 전액이 된다.
set -e
AGG=/app/reports/cg57_summary.json
while [ $# -gt 0 ]; do
  case "$1" in
    --agg-out) AGG="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cd /app

END=2026-06-24          # 학습 종료일 고정(전방 창 2026-07-29~09-23 을 OOS 로 남긴다 — CG45 의 '전방 창 부재' 완화)
TSTART=2026-03-26       # --days 90 과 동형(평가기에서 겹치는 창 제외에 쓴다)
SEEDS="0 1 2 3 4"

have_json() { [ -s "$1" ] && python -c "import json,sys; json.load(open(sys.argv[1]))" "$1" >/dev/null 2>&1; }
have_model() { [ -s "$1/xgboost_model.pkl" ] && [ -s "$1/feature_names.json" ]; }

for spec in "recency:rec" "liquidity:liq"; do
  mode="${spec%%:*}"; tag="${spec##*:}"
  od="app/models/cand_cg57_$tag"
  if have_model "$od"; then
    echo "=== train $tag ($mode) — 재사용(기존 모델) ==="
  else
    echo "=== train $tag ($mode) ==="
    OMP_NUM_THREADS=4 timeout 12600 python -u -m app.training.retrain_champion \
      --days 90 --end-date "$END" --stock-limit 200 --universe-mode "$mode" \
      --label-kind rel --horizon 5 \
      --out-dir "$od" --checkpoint-path "/app/reports/cg57_ck_$tag.pkl"
  fi
done

for s in $SEEDS; do
  for spec in "recency:rec" "liquidity:liq"; do
    mode="${spec%%:*}"; tag="${spec##*:}"
    out="app/reports/cg57_${tag}_s$s.json"
    if have_json "$out"; then
      echo "=== eval $tag seed $s — 재사용 ==="
      continue
    fi
    echo "=== eval $tag seed $s ==="
    OMP_NUM_THREADS=2 timeout 900 python -u scripts/champion_robust_eval.py \
      --model-dir "app/models/cand_cg57_$tag" \
      --train-start "$TSTART" --train-end "$END" \
      --universe training --label-kind rel --horizon 5 \
      --folds 5 --dates-per-fold 10 --stocks 60 --universe-seed "$s" \
      --out "$out"
  done
done

echo "=== 집계(rec=대조군, liq=챌린저) ==="
python scripts/champion_seed_family_agg.py --agg-out "$AGG" --expect-seeds 5 \
  --arm rec app/reports/cg57_rec_s0.json app/reports/cg57_rec_s1.json \
            app/reports/cg57_rec_s2.json app/reports/cg57_rec_s3.json app/reports/cg57_rec_s4.json \
  --arm liq app/reports/cg57_liq_s0.json app/reports/cg57_liq_s1.json \
            app/reports/cg57_liq_s2.json app/reports/cg57_liq_s3.json app/reports/cg57_liq_s4.json
echo "=== 완료: $AGG ==="
