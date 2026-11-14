#!/usr/bin/env bash
# CG277 — CG276 지평 사다리(h1>h3>h5>h10)를 **돈으로** 재측정.
#
# WHY(2026-11-14 CG276 실측, 5폴드×3시드 · panel_prod200 · 저용량 depth1):
#   OOS AUC h1 0.5442±0.0066 > h3 0.5401 > h5 0.5352 > h10 0.5325 로 완전 단조, 갭도
#   0.018/0.034/0.052/0.086 으로 같이 준다. h1−h5 Δ+0.0090 은 사전문턱 +0.02 미달이라 AUC 로는
#   '노이즈'지만, 짧은 지평은 회전율이 높아 수수료 왕복 0.21%p 가 더 자주 붙는다 → AUC 우위가
#   돈으로는 역전될 수 있다. 스킬 최우선 규칙: 축 종결도 돈 지표로 남긴다.
#
# 프로토콜(사전등록): 같은 dump·같은 폴드·같은 시드에서
#   ① h1·h5 두 arm 의 top-k − 세션 풀평균(널) 초과기대를 체결성 필터 ON · 수수료 0.21%p 차감으로 산출
#   ② 호라이즌 표(h1/h3/h5/h10)에서 초과기대 ≥ +0.1%p/세션 & 앞/뒤 절반 부호 동일 → '지평 축 생존'
#   ③ 미달이면 지평 축을 돈 근거로 종결.
#
# ⚠ 리포트는 컨테이너 안에서 쓴다(reports/overnight 는 root 소유 — 호스트 쓰기 PermissionError).
set -u
cd /home/jhshi/analyist_dd || exit 2
HOST_JSON="/home/jhshi/analyist_dd/services/xgboost-ml/reports/overnight/cg277_money_horizon.json"
PREDS="/app/reports/overnight/cg277_preds.jsonl"
SWEEP_SUM="/app/reports/overnight/cg277_sweep_summary.json"
MONEY="/app/reports/overnight/cg277_money_horizon.json"

echo "[$(date '+%F %T')] CG277 시작 — CG276_h1 · CO_smooth_d1_h5 폴드예측 덤프(5폴드×3시드)"
docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=4 timeout 2400 python -u scripts/wf_label_sweep.py \
  --panel /app/app/models/wf/panel_prod200.npz --folds 5 --seeds 3 \
  --only CG276_h1,CO_smooth_d1_h5 --dump-preds $PREDS \
  --out /app/reports/overnight/cg277_sweep.jsonl --summary-out $SWEEP_SUM" 2>&1 | tail -12
rc=${PIPESTATUS[0]}
echo "[$(date '+%F %T')] 스윕 rc=$rc"
[ $rc -eq 0 ] || exit $rc

echo "[$(date '+%F %T')] 돈 집계 — top-k vs 세션 풀평균 · 청산 호라이즌 1/3/5/10 · 수수료 0.21%p"
docker exec stock_xgboost_ml sh -c "cd /app && python -u scripts/fillable_topk_horizon_sweep.py \
  --arm-jsonl $PREDS --arm-tag CG276_h1 \
  --control-jsonl $PREDS --control-tag CO_smooth_d1_h5 \
  --k 3,5,10,20,30 --horizons 1,3,5,10 --primary 1 \
  --json-out $MONEY"
mrc=$?
echo "[$(date '+%F %T')] 돈 집계 rc=$mrc → $HOST_JSON"
exit $mrc
