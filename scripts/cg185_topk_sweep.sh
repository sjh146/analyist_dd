#!/bin/sh
# cg185_topk_sweep.sh — CG185 선택기 축: 같은 모델·같은 표본에서 top-k(3/5/10)만 바꾼 순기대.
#
# 왜 래퍼인가: model_metric_protocol_audit.py 의 --models 인자는 JSON 문자열이라
# 4단 셸(docker exec sh -c '... "[[\".."..]]"')에서 인용이 깨진 전례가 있다(CG180).
# 여기서는 인용을 한 겹으로 고정한다.
#
# 표본: data/reports/close_gate_probe/trades.csv 를 2026-11-09 까지 확장한 사본
#       (/app/app/reports/close_gate_probe_trades.csv · 113세션 · 2,251행, CG187 실측).
# 모델: champion_cand_cg183 (193피처 = drop-serving-blind, 서빙 가능 · CG186 실측)
# 출력: /app/reports/overnight/cg185_top{3,5,10}.json (+ _robust.json)
set -e
cd /app || exit 1
M='[["cand","/app/app/models/champion_cand_cg183"]]'
T=/app/app/reports/close_gate_probe_trades.csv
for K in 3 5 10; do
  echo "=== topk=$K ==="
  OMP_NUM_THREADS=2 timeout 3000 python -u scripts/model_metric_protocol_audit.py \
    --models "$M" --folds 5 --dates-per-fold 10 --stocks 80 --horizon 5 --label-kind rel \
    --topk "$K" --trades "$T" \
    --out "/app/reports/overnight/cg185_top${K}.json" \
    --robust-oos-out "/app/reports/overnight/cg185_top${K}_robust.json"
done
