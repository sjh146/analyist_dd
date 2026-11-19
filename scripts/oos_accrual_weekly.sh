#!/bin/sh
# oos_accrual_weekly.sh — 배포 챔피언 OOS 표본을 **주 단위 앵커**로 적립한다.
#
# 왜 필요한가(실측 2026-11-19): CG354 는 앵커를 2026-11-11 로 **하드코딩**해 매 실행이
# 같은 10날짜를 다시 채점했다. 원장엔 2줄이 쌓였지만 창 union 은 여전히 10날짜 →
# 표본이 늘지 않아 SE 0.0186(= 사전문턱 +0.02 와 동급)에서 진전이 0 이었다.
# 해상 목표는 SE 0.010 → 35날짜, SE 0.007 → 71날짜다. 표본은 캘린더로만 늘어난다.
#
# 앵커 규칙: **직전 금요일**(금요일 15:30 이후 실행이면 당일). 같은 주 안에서는 앵커가
# 고정되므로 창이 밀리지 않고(프로토콜 불변), 주가 바뀌면 새 날짜가 꼬리에 붙는다.
# 중복 적립은 oos_accrual_log.py 가 (앵커, 폴드창) 키로 건너뛰고 누적은 창 union 으로 센다.
#
# 고정값(train-end 2026-10-01 · oos-start 2026-10-02 · champion · folds 2×5 · seed 0)은
# CG354 와 동일하게 유지한다 — 앵커만 움직여야 적립이 '표본'이지 '프로토콜 변경'이 아니다.
set -e
cd "$(dirname "$0")/.."

ANCHOR=$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
KST = timezone(timedelta(hours=9))
now = datetime.now(KST)
# 직전 금요일: 금요일이면 오늘(장 마감 후 실행 전제), 아니면 지난 금요일
back = (now.weekday() - 4) % 7
d = (now - timedelta(days=back)).date()
print(d.isoformat())
PY
)
echo "=== OOS 적립 앵커: ${ANCHOR} ==="

OUT="/app/reports/overnight/oos_accrual_${ANCHOR}.json"
docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=4 timeout 2550 python -u scripts/champion_robust_eval.py \
  --model-dir app/models/champion --folds 2 --dates-per-fold 5 \
  --train-end 2026-10-01 --oos-start 2026-10-02 --asof-date ${ANCHOR} \
  --universe training --universe-seed 0 --out ${OUT}"

python3 scripts/oos_accrual_log.py --append "services/xgboost-ml${OUT#/app}"
python3 scripts/oos_accrual_log.py --show
