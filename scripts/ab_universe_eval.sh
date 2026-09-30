#!/usr/bin/env bash
# ab_universe_eval.sh — champion_robust_eval 의 **표본 유니버스 축 A/B** (학습동형 vs 유동성).
#
# 왜(실측 2026-09-30, rank_universe_check + DB 조회):
#   배포 챔피언의 '자기 과제 OOS 0.4410'(CG37)은 **다른 도메인 채점**이었다.
#   champion_robust_eval 의 표본은 '유동성 상위 80종목'인데 그중 26개가 ETF/ETN/레버리지
#   (KODEX 200·KODEX 레버리지·TIGER 200·단일종목 레버리지 …)이고, 학습 유니버스
#   (select_training_universe 200종목, ETF/ETN·파생 제외)와의 **교집합이 5종목**뿐이었다.
#   모델은 소형주 200종목으로 학습됐는데 대형주+ETF 표본으로 채점된 것이다.
#   또한 6개 rank_* 피처는 학습 시 200종목 유니버스 기준, 평가 시 80종목 표본 기준으로
#   재척도된다(실측 mean|Δrank| 0.0875 — return_20d 0.0218 ~ volume_ratio_20 0.137).
#
# 무엇을 하는가: 같은 모델·같은 폴드·같은 날짜·같은 종목 수에서 유니버스 선택만 바꿔 두 번 채점한다.
#   arm1 --universe training   (학습 경로와 동형)
#   arm2 --universe liquidity  (현행 프로토콜)
# 결과: services/xgboost-ml/reports/champion_robust_eval_{trainuni,liq60}.json
#
# 실행: setsid nohup bash scripts/ab_universe_eval.sh >/dev/null 2>&1 & disown
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1
LOG=data/reports/me_cycle/ab_universe_eval.log

echo "[$(date '+%F %T')] 대기: 컨테이너의 선행 진단(rank_universe_check) 종료 대기 — 직렬화" >> "$LOG"
for _ in $(seq 1 120); do
  if ! docker exec stock_xgboost_ml sh -c 'ps -ef' 2>/dev/null | grep -q "[r]ank_universe_check"; then
    break
  fi
  sleep 30
done

echo "[$(date '+%F %T')] arm1 시작: --universe training (folds=3 dates=10 stocks=60)" >> "$LOG"
docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 5400 python -u scripts/champion_robust_eval.py --universe training --folds 3 --dates-per-fold 10 --stocks 60 --out app/reports/champion_robust_eval_trainuni.json' >> "$LOG" 2>&1
echo "[$(date '+%F %T')] arm1 rc=$?" >> "$LOG"

echo "[$(date '+%F %T')] arm2 시작: --universe liquidity (현행 프로토콜)" >> "$LOG"
docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 5400 python -u scripts/champion_robust_eval.py --universe liquidity --folds 3 --dates-per-fold 10 --stocks 60 --out app/reports/champion_robust_eval_liq60.json' >> "$LOG" 2>&1
echo "[$(date '+%F %T')] arm2 rc=$?" >> "$LOG"
echo "[$(date '+%F %T')] A/B 완료 — 다음 세션: python3 scripts/model_engineer_cycle.py --ingest CG40 --log data/reports/me_cycle/ab_universe_eval.log" >> "$LOG"
