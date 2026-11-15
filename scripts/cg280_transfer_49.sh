#!/usr/bin/env bash
# CG280 — 전이 검정: CG278/279 의 +1.70%p/5일 초과기대가 panel_prod200(200종목) 특이인지
#         같은 arm·같은 프로토콜을 49종목 청정 패널(panel_420_asofpatch)에서 재측정한다.
#
# WHY(2026-11-14 CG278 실측): 배포가능 arm(CO_smooth_d1_h5)이 전 유니버스 풀평균을 h5 k3 에서
#   +1.703%p/세션(t 3.50) 이겼다. 그런데 CG11/CG5 에서 같은 config 가 유니버스를 바꾸면 부호가
#   뒤집혔다(49종목 +0.0227 → 150종목 -0.0109 AUC). 연 ~90%p 는 이례적이라 생존편향(현 시총 기준
#   유니버스 = L5c 미해결) 또는 200종목 특이 구조일 수 있다 → 같은 프로토콜로 49종목 패널 재측정.
#
# 프로토콜(사전등록): 같은 폴드 5 · 시드 3 · exit close_h · 수수료 왕복 0.21%p · 체결성 필터 ON ·
#   널 = 세션 풀평균(49종목 패널 전 유니버스, --dump-all-codes).
#   성공: h5 k3 Δ(top-k − 풀평균) ≥ +0.1%p/세션 & t ≥ 2 & 앞/뒤 절반 부호 동일 → '전이됨'
#   Δ ≤ 0 이면 '유니버스 특이'로 종결하고 L5c(PIT 유니버스)를 사람 승인 항목으로 올린다.
set -u
cd /home/jhshi/analyist_dd || exit 2
HOST_JSON="/home/jhshi/analyist_dd/services/xgboost-ml/reports/overnight/cg280_transfer_49.json"
PREDS="/app/reports/overnight/cg280_preds_49.jsonl"
MONEY="/app/reports/overnight/cg280_transfer_49.json"

echo "[$(date '+%F %T')] CG280 시작 — 49종목 패널 덤프(2 arm × 5폴드 × 3시드)"
docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=4 timeout 2400 python -u scripts/wf_label_sweep.py \
  --panel /app/app/models/wf/panel_420_asofpatch.npz --folds 5 --seeds 3 \
  --only CO_smooth_d1_h5,CO_core30_h5 --dump-preds $PREDS --dump-all-codes \
  --out /app/reports/overnight/cg280_sweep.jsonl --summary-out /app/reports/overnight/cg280_sweep_summary.json" 2>&1 | tail -12
rc=${PIPESTATUS[0]}
echo "[$(date '+%F %T')] 스윕 rc=$rc"
[ $rc -eq 0 ] || exit $rc

echo "[$(date '+%F %T')] 돈 집계 — CO_smooth_d1_h5 top-k vs 49종목 세션 풀평균"
docker exec stock_xgboost_ml sh -c "cd /app && python -u scripts/fillable_topk_horizon_sweep.py \
  --arm-jsonl $PREDS --arm-tag CO_smooth_d1_h5 \
  --control-jsonl $PREDS --control-tag CO_core30_h5 \
  --k 3,5,10 --horizons 1,5 --primary 5 \
  --json-out $MONEY"
mrc=$?
echo "[$(date '+%F %T')] 돈 집계 rc=$mrc"
if [ $mrc -eq 0 ]; then
  docker cp stock_xgboost_ml:$MONEY "$HOST_JSON" >/dev/null 2>&1 && echo "[$(date '+%F %T')] 호스트 복사 → $HOST_JSON"
fi
exit $mrc
