#!/usr/bin/env bash
: "${PROJ_DIR:=${PROJ_DIR}}"
# CG214 — 돈 지표 **표본 확대**: 패널 walk-forward 덤프(≈200세션)로 top-k 순기대를 잰다.
#
# 왜 (2026-11-13 01:0x 엔지니어 자율 틱, CG213 실측 근거)
#   CG211~CG213 의 모든 돈 판정 표본은 발행 아티팩트 11세션뿐이다. k=3 top 의 세션 sd 는 2.1153%p
#   → SE 0.638 → t=2 최소검출효과(MDE) **±1.276%p**. 승격 돈 게이트 문턱은 +0.1%p 이므로
#   **문턱의 12.8배**다. 즉 11세션 표본에서는 0.1~0.5%p 개선이 원리적으로 검출 불가이고,
#   그동안의 '노이즈' 판정은 '미검출'과 구분되지 않는다(스킬 최우선 규칙 ②의 실질 위반 위험).
#   대책: wf_label_sweep 의 --dump-preds 로 **폴드 테스트창 전량**(5폴드, 2025-11~2026-09)을 덤프해
#   같은 계측기(fillable_topk_expectancy)로 재면 세션 수가 11 → 150~200 으로 늘어 MDE 가 ±0.3%p 대로 내려간다.
#
# 짝 설계(같은 런·같은 패널·같은 폴드·같은 시드):
#   arm     = CO_smooth_d1_h5   (게이트 ON + 라벨 스무딩 + depth1·lr0.05 = 배포가능 최고 조정 arm)
#   control = CO_core30_h5      (게이트 ON + 분위0.3 h5 top30 = 같은 런 대조군)
#   → 짝 Δ 는 '스무딩+depth1 단독' 기여를 **돈**으로 잰 값이다(AUC 판정 아님).
#
# 재현: bash scripts/cg214_money_replay.sh
set -uo pipefail
cd ${PROJ_DIR} || exit 1

PANEL=/app/app/models/wf/panel_prod200.npz
OUT=/app/reports/overnight/cg214_sweep.jsonl
SUM=/app/reports/overnight/cg214_money.json
PREDS=/app/reports/overnight/cg214_preds.jsonl
SWEEP_SUM=/app/reports/overnight/cg214_sweep_summary.json

echo "[cg214] start $(date -Is) $(uptime | sed 's/.*load/load/')"
rm -f services/xgboost-ml/reports/overnight/cg214_money.json

# ── 1) 폴드 테스트창 전량 예측 덤프 (학습은 이 단계에서만; 시드 1 = 돈 지표는 시드 무관)
docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=4 timeout 3600 python -u scripts/wf_label_sweep.py \
  --panel $PANEL --days 420 --folds 5 --seeds 1 \
  --only CO_core30_h5,CO_smooth_d1_h5 \
  --out $OUT --summary-out $SWEEP_SUM --dump-preds $PREDS" 2>&1 | tail -25
rc1=${PIPESTATUS[0]}
rows=$(docker exec stock_xgboost_ml sh -c "wc -l < $PREDS" 2>/dev/null | tr -d ' ')
echo "[cg214] sweep rc=$rc1 preds_rows=${rows:-0}"
if [ "${rc1:-1}" -ne 0 ] || [ -z "${rows:-}" ] || [ "${rows:-0}" -lt 20000 ]; then
  echo "[cg214] FAIL 덤프 부족(rc=$rc1 rows=${rows:-0}) — 판정 불가로 기록해야 한다"
  exit 2
fi

# ── 2) 같은 계측기로 돈 지표 (체결성 필터 + 수수료 왕복 0.21%p + 익일시가 청산)
docker exec stock_xgboost_ml sh -c "cd /app && timeout 1800 python -u scripts/fillable_topk_expectancy.py \
  --arm-jsonl $PREDS --arm-tag CO_smooth_d1_h5 \
  --control-jsonl $PREDS --control-tag CO_core30_h5 \
  --k 3,5,10 --sides top,bottom --exit next_open --horizon 1 \
  --exclude-limit-up --max-day-chg 25 --min-value 1e9 \
  --json-out $SUM" 2>&1 | tail -30
rc2=${PIPESTATUS[0]}
echo "[cg214] money rc=$rc2 $(date -Is)"
[ "$rc2" -eq 0 ] || exit 3

ls -la services/xgboost-ml/reports/overnight/cg214_money.json 2>/dev/null
exit 0
