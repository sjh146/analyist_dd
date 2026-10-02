#!/usr/bin/env bash
# CG9 A단계 런처 (2026-10-02) — 추론 계약 무변경 배포 가능 후보의 **생산 경로 검증**.
#
# 목적(주): 생산 유니버스(200종목·90일)에서 rel_smooth h5 + depth1 후보가 실제로 만들어지고
#           `champion_promote --dry-run` 을 통과하는가 = 배포성 판정.
# 목적(보조): 같은 창·같은 프로토콜로 현행 라벨(h1_direction) 대조군과 짝 Δ·t/SE 기록.
#   ⚠ 학습구간 이전 창 3개뿐이라 SE≈0.08 → **AUC 판정에는 쓰지 말 것**(CG38 실측).
#
# 왜 스크립트인가: `--model-params '{"max_depth":1,...}'` 를 구동기 커맨드 문자열에 직접 넣으면
#   중첩 인용(`docker exec sh -c '...'`)에서 JSON 이 깨진다(2026-10-02 실측: unexpected EOF).
#   따라서 JSON 은 여기 리터럴로 두고, 구동기에는 `--out <arm.json>` 만 노출한다(요약 경로 파싱용).
#
# 사용(구동기/수동): docker exec stock_xgboost_ml bash /app/scripts/cg9_run.sh --out /app/reports/overnight/cg9_arm.json
set -u
OUT_ARM="/app/reports/overnight/cg9_arm.json"
OUT_CTL="/app/reports/overnight/cg9_ctl.json"
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT_ARM="$2"; shift 2 ;;
    *) shift ;;
  esac
done

cd /app || exit 1

echo "[cg9] 1/3 후보 학습 시작 $(date -u +%H:%M:%S)Z"
python -m app.training.retrain_champion \
  --days 90 --end-date 2026-10-01 --stock-limit 200 \
  --label-kind rel_smooth --horizon 5 \
  --model-params '{"max_depth":1,"learning_rate":0.05}' \
  --checkpoint-path /app/app/models/wf/cg9.ckpt \
  --out-dir app/models/challenger_cg9 || echo "[cg9] 후보 학습 실패(계속)"

echo "[cg9] 2/3 arm 평가 → $OUT_ARM"
python /app/scripts/champion_robust_eval.py \
  --model-dir app/models/challenger_cg9 \
  --train-start 2026-07-03 --train-end 2026-10-01 \
  --folds 5 --dates-per-fold 10 --stocks 80 \
  --label-kind rel --horizon 5 --out "$OUT_ARM"

echo "[cg9] 3/3 ctl 평가 → $OUT_CTL"
python /app/scripts/champion_robust_eval.py \
  --model-dir app/models/champion_cand \
  --train-start 2026-07-03 --train-end 2026-10-01 \
  --folds 5 --dates-per-fold 10 --stocks 80 \
  --label-kind rel --horizon 5 --out "$OUT_CTL"

echo "[cg9] 완료 $(date -u +%H:%M:%S)Z — 짝 Δ 는 arm−ctl 을 직접 계산하라(첫 --out 만 구동기가 판정에 읽는다)"
