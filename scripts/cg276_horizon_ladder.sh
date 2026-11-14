#!/usr/bin/env bash
# CG276 — 라벨 **지평** 사다리(h1 · h3 · h5 · h10): 정보 상한이 지평의 성질인지 피처의 성질인지.
#
# WHY(2026-11-14 CG275 실측): 저용량 arm(CO_smooth_d1_h5)에서 train 0.587 vs OOS 0.5352
# (갭 0.052)인데 OOS 는 기본 용량 arm(0.5323)과 사실상 같았다 → 학습셋을 외워서 생긴 갭이 아니라
# **일반화되는 신호 자체가 ~0.53 수준**. 그러면 다음 질문은 "그 0.53 이 h5 라벨의 성질인가"다.
# 같은 피처·같은 저용량(depth1+스무딩)·같은 폴드에서 지평만 바꿔 OOS AUC 를 비교한다.
#
# 판정(사전등록, 구동기 judge_horizon_ladder): 기준 arm CO_smooth_d1_h5(h5) 대비 OOS Δ ≥ +0.02
# → 신호. 미달이면 '지평 축 종결'. ⚠ AUC 는 정보량 비교이고 돈 지표가 아니다(지평별 거래비용 다름).
#
# ⚠ 리포트는 컨테이너 안에서 쓴다(reports/overnight 는 root 소유 — 호스트 쓰기 PermissionError).
set -u
cd /home/jhshi/analyist_dd || exit 2
REPORT_HOST="/home/jhshi/analyist_dd/services/xgboost-ml/reports/overnight/cg276_horizon_ladder.json"
SWEEP_JSONL="/app/reports/overnight/cg276_horizon_sweep.jsonl"
SWEEP_SUM="/app/reports/overnight/cg276_horizon_sweep_summary.json"

echo "[$(date '+%F %T')] CG276 시작 — 지평 사다리 h1/h3/h5/h10 × 5폴드 × 3시드"
docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=4 timeout 2400 python -u scripts/wf_label_sweep.py \
  --panel /app/app/models/wf/panel_prod200.npz --folds 5 --seeds 3 \
  --only CG276_h1,CG276_h3,CO_smooth_d1_h5,CG276_h10 --report-train-auc \
  --out $SWEEP_JSONL --summary-out $SWEEP_SUM" 2>&1 | tail -20
rc=${PIPESTATUS[0]}
echo "[$(date '+%F %T')] 스윕 rc=$rc"

docker exec stock_xgboost_ml sh -c "cd /app && python -u scripts/_insample_gap_report.py \
  --summary $SWEEP_SUM --out /app/reports/overnight/cg276_horizon_ladder.json"
rrc=$?
echo "[$(date '+%F %T')] 리포트 rc=$rrc → $REPORT_HOST"
[ $rc -eq 0 ] || exit $rc
exit $rrc
