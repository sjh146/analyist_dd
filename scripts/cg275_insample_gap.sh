#!/usr/bin/env bash
# CG275 — in-sample AUC vs OOS AUC 갭(GD) 실측: '정보 상한' 과 '용량/과적합' 을 가른다.
#
# WHY(2026-11-14): 19사이클 연속 로버스트 AUC Δ+0.02 미달. 조정 축 상한 +0.011, 데이터 축
# 스크린 4칸(89컬럼 × 2패널 × 2라벨) 문턱 통과 0개. 남은 이분(二分)은 "피처에 일반화되는 신호가
# 없는가(정보 상한)" vs "모델이 학습셋을 외우는가(용량·과적합)" 이고 처방이 정반대다.
#
# 두 arm 을 **같은 폴드·같은 시드**로 돌려 용량 gradient 를 만든다:
#   CO_core30_h5      — 기본 용량 = 대조군
#   CO_smooth_d1_h5   — depth1(용량 축소) + 라벨 스무딩 = 저용량 arm
# 사전등록 판정(항목 success): 갭 ≥0.30 → 정보 상한(데이터 투자) / 갭 ≤0.10 → 용량·표현력 투자.
#
# ⚠ 리포트는 **컨테이너 안에서** 쓴다: reports/overnight 는 docker 가 root 로 만든 디렉터리라
#   호스트 jhshi 로는 PermissionError 가 난다(실측 2026-11-14 17:10 — 스윕은 rc=0 완주했는데
#   리포트 쓰기에서 죽어 원장에 '측정값 없음'으로 오기록됐다). 스크립트는 /app/scripts 로 마운트돼 있다.
#
# 산출물(고정 경로 — 항목 summary_out 과 일치해야 한다):
#   services/xgboost-ml/reports/overnight/cg275_insample_gap.json
set -u
cd /home/jhshi/analyist_dd || exit 2
REPORT_HOST="/home/jhshi/analyist_dd/services/xgboost-ml/reports/overnight/cg275_insample_gap.json"
SWEEP_JSONL="/app/reports/overnight/cg275_insample_sweep.jsonl"
SWEEP_SUM="/app/reports/overnight/cg275_insample_sweep_summary.json"

echo "[$(date '+%F %T')] CG275 시작 — 2 arm × 5폴드 × 3시드 (--report-train-auc)"
docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=4 timeout 1800 python -u scripts/wf_label_sweep.py \
  --panel /app/app/models/wf/panel_prod200.npz --folds 5 --seeds 3 \
  --only CO_core30_h5,CO_smooth_d1_h5 --report-train-auc \
  --out $SWEEP_JSONL --summary-out $SWEEP_SUM" 2>&1 | tail -25
rc=${PIPESTATUS[0]}
echo "[$(date '+%F %T')] 스윕 rc=$rc"

docker exec stock_xgboost_ml sh -c "cd /app && python -u scripts/_insample_gap_report.py \
  --summary $SWEEP_SUM --out /app/reports/overnight/cg275_insample_gap.json"
rrc=$?
echo "[$(date '+%F %T')] 리포트 rc=$rrc → $REPORT_HOST"
# 스윕이 실패했으면 리포트 성공 여부와 무관하게 실패로 남긴다(가짜 '판정' 방지).
[ $rc -eq 0 ] || exit $rc
exit $rrc
