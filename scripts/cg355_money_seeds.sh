#!/bin/sh
# CG355 — 배포 챔피언 vs 재학습 후보(cg346) 3시드 짝 순기대 재측정.
#
# 왜 스크립트로 빼는가: 원 등록 명령은 `sh -c 'cd /app && for s in 0 1 2; do ...; done'`
# 였는데 벽시계 상한(`timeout`)이 없어 구동기 자기모순 가드에 걸렸다(CG355 경고).
# 한 줄 안에서 변수 ${s} 를 쓰는 루프에 timeout 을 씌우려면 인용이 겹쳐 오히려 깨지기
# 쉬우므로, 재현 가능한 스크립트로 고정하고 명령은 `timeout 3450 bash scripts/...` 로 둔다.
# est_minutes=35 → 필요 벽시계 = 35*60*1.5+300 = 3450s (구동기 required_timeout_seconds).
#
# 입력: cg352 3시드 arm/control 예측 jsonl (같은 OOS 창 2026-10-02~2026-11-04, 창 앵커 2026-11-11)
# 출력: /app/reports/overnight/cg352_money_s{0,1,2}.json
# 체결성 필터 + 수수료 왕복 0.21%p 는 fillable_topk_expectancy.py 기본 경로가 적용한다.
set -e
cd /app

for s in 0 1 2; do
  echo "=== seed ${s} ==="
  python -u scripts/fillable_topk_expectancy.py \
    --arm-jsonl "/app/reports/overnight/cg352_retrain_cg346_s${s}.jsonl" \
    --arm-tag "retrain_cg346_s${s}" \
    --control-jsonl "/app/reports/overnight/cg352_champion_s${s}.jsonl" \
    --control-tag "champion_s${s}" \
    --k 3,5,10 --exit next_open --horizon 1 \
    --json-out "/app/reports/overnight/cg352_money_s${s}.json" || exit 1
done
echo "=== CG355 done: 3 seeds ==="
