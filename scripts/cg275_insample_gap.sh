#!/usr/bin/env bash
# CG275 — in-sample AUC vs OOS AUC 갭(GD) 실측: '정보 상한' 과 '과적합/분산' 을 가른다.
#
# WHY(2026-11-14): 19사이클 연속 로버스트 AUC Δ+0.02 미달. 조정 축 상한 +0.011, 데이터 축
# 스크린 4칸(89컬럼 × 2패널 × 2라벨) 문턱 통과 0개. 남은 이분(二分)은 "데이터에 신호가 없는가
# (정보 상한)" vs "모델이 못 일반화하는가(분산)" 이고 처방이 정반대다.
#
# 두 arm 을 **같은 폴드·같은 시드**로 돌려 용량 gradient 를 만든다:
#   CO_core30_h5      — 기본 용량(depth 다름·라벨 원형) = 대조군
#   CO_smooth_d1_h5   — depth1(용량 축소) + 라벨 스무딩 = 저용량 arm
# 해석: 저용량 arm 에서 갭이 줄고 OOS 가 유지되면 '분산 문제'(용량·정규화 투자),
#       두 arm 의 갭이 모두 크고 OOS 가 0.53 대에 머물면 '정보 상한'(데이터 축만 남음).
#
# 산출물(고정 경로 — 구동기 summary_path 기본값과 일치해야 한다):
#   services/xgboost-ml/reports/overnight/cg275_insample_gap.json
set -u
cd /home/jhshi/analyist_dd || exit 2
OUT_HOST="/home/jhshi/analyist_dd/services/xgboost-ml/reports/overnight"
SWEEP_JSONL="/app/reports/overnight/cg275_insample_sweep.jsonl"
SWEEP_SUM="/app/reports/overnight/cg275_insample_sweep_summary.json"
REPORT="$OUT_HOST/cg275_insample_gap.json"

echo "[$(date '+%F %T')] CG275 시작 — 2 arm × 5폴드 × 3시드 (--report-train-auc)"
docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=4 timeout 1800 python -u scripts/wf_label_sweep.py \
  --panel /app/app/models/wf/panel_prod200.npz --folds 5 --seeds 3 \
  --only CO_core30_h5,CO_smooth_d1_h5 --report-train-auc \
  --out $SWEEP_JSONL --summary-out $SWEEP_SUM" 2>&1 | tail -25
rc=${PIPESTATUS[0]}
echo "[$(date '+%F %T')] 스윕 rc=$rc"

python3 scripts/_insample_gap_report.py \
  --summary "$OUT_HOST/cg275_insample_sweep_summary.json" \
  --out "$REPORT"
rrc=$?
echo "[$(date '+%F %T')] 리포트 rc=$rrc → $REPORT"
# 스윕이 실패했으면 리포트 성공 여부와 무관하게 실패로 남긴다(가짜 '판정' 방지).
[ $rc -eq 0 ] || exit $rc
exit $rrc
