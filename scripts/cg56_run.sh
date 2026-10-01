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
#
# 재개 지원(실측 2026-10-01 CG56 1차): 바깥 `timeout 5400` 이 실측 소요(20회 × 5.1분 ≈ 102분)에
# 12분 모자라 시드 9 cand 가 **시작조차 못 하고** rc=124 로 죽었다. 그런데 그 시점에 20회 중
# 19회가 디스크에 남아 있었다(게다가 `timeout` 은 `sh` 만 죽여 python 자식은 고아로 완주했다 —
# 시드 9 champ JSON 이 18:42 에 생겼다). 종전 스크립트는 전량을 매번 다시 돌리는 구조라
# 재시도 1회 비용이 100분이었다 → **이미 있는 산출물은 건너뛴다**(신호·비용 양쪽에서 재개가 필수).
# 유효성 판정: JSON 이 파싱되고 jsonl 이 비어있지 않아야 한다(중간에 잘린 파일 배제).
# ⚠ 부분 저장 원자성은 없다 — eval 1회가 중간에 죽으면 그 시드의 jsonl 만 남고 json 이 없거나
#    그 반대일 수 있으므로 반드시 **두 파일 다** 확인한다.
set -e
OUT=/app/reports/cg56_summary.json
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cd /app

have() {
  [ -s "$1" ] && [ -s "$2" ] && \
    python -c "import json,sys; json.load(open(sys.argv[1]))" "$1" >/dev/null 2>&1
}

for s in 0 1 2 3 4 5 6 7 8 9; do
  cj="app/reports/cg56_champ_s$s.json"
  cl="/app/reports/cg56_champion_s$s.jsonl"
  if have "$cj" "$cl"; then
    echo "=== seed $s : champion — 재사용(기존 산출물) ==="
  else
    echo "=== seed $s : champion ==="
    OMP_NUM_THREADS=2 timeout 1200 python -u scripts/champion_robust_eval.py \
      --model-dir app/models/champion --train-start 2026-06-25 --train-end 2026-09-30 \
      --universe training --label-kind rel --horizon 5 --folds 5 --dates-per-fold 10 \
      --stocks 60 --universe-seed "$s" \
      --out "$cj" \
      --dump-preds "$cl" --dump-tag champion
  fi
  dj="app/reports/cg56_cand_s$s.json"
  dl="/app/reports/cg56_cand_s$s.jsonl"
  if have "$dj" "$dl"; then
    echo "=== seed $s : cand_cg51 — 재사용(기존 산출물) ==="
  else
    echo "=== seed $s : cand_cg51 ==="
    OMP_NUM_THREADS=2 timeout 1200 python -u scripts/champion_robust_eval.py \
      --model-dir app/models/cand_cg51 --train-start 2026-06-25 --train-end 2026-09-30 \
      --universe training --label-kind rel --horizon 5 --folds 5 --dates-per-fold 10 \
      --stocks 60 --universe-seed "$s" \
      --out "$dj" \
      --dump-preds "$dl" --dump-tag cand_cg51
  fi
done
echo "=== 집계(rank-avg 결합) ==="
OMP_NUM_THREADS=2 python -u scripts/blend_eval.py --dir /app/reports --prefix cg56 \
  --seeds 0-9 --champ-tag champion --cand-tag cand --out "$OUT"
echo "=== 완료: $OUT ==="
