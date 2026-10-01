#!/bin/sh
# cg56_run.sh — CG56 라벨 다양성 앙상블(챔피언 h1-abs + cand_cg51 rel-h5) 짝 10시드 실행기.
#
# 왜 셸 래퍼인가: 구동기 `_out_arg` 는 커맨드 문자열에서 **첫 --out 을 그대로** 읽는다.
# for 루프 안에서 `--out ..._s$s.json` 처럼 셀 변수를 쓰면 구동기가 리터럴로 해석하지 못해
# 결과가 실재해도 '판정불가 · 요약 없음' 으로 기록된다(실측 2026-10-01 CG55 의 재발).
# 그래서 시드별 --out 은 이 파일 안에 두고, 커맨드에는 **최종 집계 경로 하나만** 리터럴로 남긴다.
#
# 두 모델을 반드시 같은 런·같은 시드·같은 창으로 덤프해야 결합이 성립한다.
# 창 제외 구간(--train-start/--train-end)은 챔피언(06-25~09-23)·후보(07-02~09-30) 를 모두
# 덮는 합집합(06-25~09-30)으로 **동일하게** 준다 — 다르면 살아남는 창 집합이 달라져 짝이 깨진다.
set -e
OUT=/app/reports/cg56_summary.json
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cd /app
for s in 0 1 2 3 4 5 6 7 8 9; do
  echo "=== seed $s : champion ==="
  OMP_NUM_THREADS=2 timeout 1200 python -u scripts/champion_robust_eval.py \
    --model-dir app/models/champion --train-start 2026-06-25 --train-end 2026-09-30 \
    --universe training --label-kind rel --horizon 5 --folds 5 --dates-per-fold 10 \
    --stocks 60 --universe-seed "$s" \
    --out "app/reports/cg56_champ_s$s.json" \
    --dump-preds "/app/reports/cg56_champion_s$s.jsonl" --dump-tag champion
  echo "=== seed $s : cand_cg51 ==="
  OMP_NUM_THREADS=2 timeout 1200 python -u scripts/champion_robust_eval.py \
    --model-dir app/models/cand_cg51 --train-start 2026-06-25 --train-end 2026-09-30 \
    --universe training --label-kind rel --horizon 5 --folds 5 --dates-per-fold 10 \
    --stocks 60 --universe-seed "$s" \
    --out "app/reports/cg56_cand_s$s.json" \
    --dump-preds "/app/reports/cg56_cand_s$s.jsonl" --dump-tag cand_cg51
done
echo "=== 집계(rank-avg 결합) ==="
OMP_NUM_THREADS=2 python -u scripts/blend_eval.py --dir /app/reports --prefix cg56 \
  --seeds 0-9 --champ-tag champion --cand-tag cand --out "$OUT"
echo "=== 완료: $OUT ==="
