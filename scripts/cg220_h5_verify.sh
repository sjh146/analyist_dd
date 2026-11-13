#!/usr/bin/env bash
: "${PROJ_DIR:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# CG220 — h5(5일 보유) 축 **검증**: 5시드 앙상블·8폴드로 재측정하고 체결성 필터를 적용한 비중첩 표본으로 확인.
#
# 왜 (2026-11-13 01:5x, CG216~CG219 실측)
#   h1(next_open) 청산: top-k 백분위 0.497~0.509 = 무작위 k 와 구분 불가(225세션) → 배포 경로에 정보 없음.
#   같은 덤프를 close_h h5 로 재면: 비중첩 Δ+2.391%p(t 3.38, n45) · 폴드 5/5 동일부호 · h10 +4.159(t 3.25).
#   대조군: quality_roa 고정바스켓 Δ−0.012(t −0.05, 겹침 6.2%) · return_5d 모멘텀 바스켓 Δ+0.316(t 0.63)
#   → '정적 종목효과'도 '단순 모멘텀'도 아니다. 단일 시드·단일 폴드격자 결과이므로 시드·폴드를 늘려
#     평균이 유지되는지 확인해야 한다(스킬: '+0.02 돌파가 분산 축소 산물인지 먼저 분해하라').
#
# 판정(사전 등록): 비중첩 h5 k=5 Δ ≥ +0.5%p & t ≥ 2 & 폴드 부호 6/8 이상 → h5 축을 검증통과로 표시.
#   미달이면 h5 축도 종결(단일격자 우연). rc≠0/요약 미갱신 = 실행실패.
set -uo pipefail
cd ${PROJ_DIR} || exit 1

PANEL=/app/app/models/wf/panel_prod200.npz
PREDS=/app/reports/overnight/cg220_preds.jsonl
SWEEP_SUM=/app/reports/overnight/cg220_sweep_summary.json
OUT=/app/reports/overnight/cg220_hold.json
NULL=/app/reports/overnight/cg220_null_h5.json

echo "[cg220] start $(date -Is) $(uptime | sed 's/.*load/load/')"
rm -f services/xgboost-ml/reports/overnight/cg220_hold.json

docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=4 timeout 5400 python -u scripts/wf_label_sweep.py \
  --panel $PANEL --days 420 --folds 8 --seeds 5 \
  --only CO_core30_h5,CO_smooth_d1_h5 \
  --out /app/reports/overnight/cg220_sweep.jsonl --summary-out $SWEEP_SUM --dump-preds $PREDS" 2>&1 | tail -25
rc1=${PIPESTATUS[0]}
rows=$(docker exec stock_xgboost_ml sh -c "wc -l < $PREDS" 2>/dev/null | tr -d ' ')
echo "[cg220] sweep rc=$rc1 preds_rows=${rows:-0}"
if [ "${rc1:-1}" -ne 0 ] || [ "${rows:-0}" -lt 20000 ]; then
  echo "[cg220] FAIL 덤프 부족(rc=$rc1 rows=${rows:-0})"; exit 2
fi

docker exec stock_xgboost_ml sh -c "cd /app && timeout 900 python -u scripts/_cg218_hold.py \
  --panel $PANEL --preds $PREDS --tag CO_smooth_d1_h5 --ks 5,10 --horizons 1,5 --out $OUT" 2>&1 | tail -10
rc2=${PIPESTATUS[0]}

docker exec stock_xgboost_ml sh -c "cd /app && timeout 900 python -u scripts/_cg216_null.py \
  --jsonl $PREDS --tags CO_smooth_d1_h5 --k 5,10 --exit close_h --horizon 5 --out $NULL" 2>&1 | tail -8

echo "[cg220] done rc_hold=$rc2 $(date -Is)"
[ "$rc2" -eq 0 ] || exit 3
exit 0
