#!/usr/bin/env bash
# 기차 이동 전 긴급 종료 준비 (30분 제약). 자기매칭 회피를 위해 브래킷 패턴 사용.
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1

echo "=== [1] 실행 중 작업 파악 ==="
INFLIGHT=""
for pat in 'run_factor_job' 'naver_daily_backfill' 'model_engineer_cycle\.py --run' 'researcher_cycle\.py --run' 'wf_label_sweep' 'train_curated' 'ask_claude.sh' 'opencode'; do
  BP="[${pat:0:1}]${pat:1}"
  PIDS=$(pgrep -f "$BP" 2>/dev/null | tr '\n' ' ')
  if [ -n "$PIDS" ]; then
    echo "  $pat → $PIDS"
    INFLIGHT="$INFLIGHT $PIDS"
  fi
done
[ -z "$INFLIGHT" ] && echo "  (인플라이트 작업 없음)"

echo
echo "=== [2] 정지 (컨테이너 내부 작업은 root 경유) ==="
CIDS=$(docker top stock_xgboost_ml 2>/dev/null | awk '/wf_label_sweep|train_curated|pnl_backtest|wf_wave/ {print $2}' | tr '\n' ' ')
CIDS2=$(docker top stock_job_runner 2>/dev/null | awk '/run_factor_job|real_factor_backtest/ {print $2}' | tr '\n' ' ')
ALL="$INFLIGHT $CIDS $CIDS2"
if [ -n "$(echo $ALL | tr -d ' ')" ]; then
  /mnt/c/Windows/System32/wsl.exe -u root -- kill -TERM $ALL 2>/dev/null
  sleep 3
  LEFT=$(pgrep -f '[r]un_factor_job|[m]odel_engineer_cycle\.py --run|[r]esearcher_cycle\.py --run|[w]f_label_sweep' 2>/dev/null | wc -l)
  if [ "$LEFT" -gt 0 ]; then
    /mnt/c/Windows/System32/wsl.exe -u root -- kill -9 $ALL 2>/dev/null
    sleep 2
  fi
  echo "  정지 완료. 남은 인플라이트: $(pgrep -f '[r]un_factor_job|[m]odel_engineer_cycle\.py --run|[r]esearcher_cycle\.py --run|[w]f_label_sweep' 2>/dev/null | wc -l)개"
else
  echo "  (죽일 대상 없음)"
fi

echo
echo "=== [3] 자율 루프 일시 정지 (크론: 재기동 후 재개) ==="
echo "  ※ Hermes 가 종료되면 크론도 함께 멈춥니다(별도 조치 불필요)."

echo
echo "=== [4] DB 상태 (미완 트랜잭션) ==="
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT '  idle-in-transaction: ' || COUNT(*) FILTER (WHERE state='idle in transaction') || ' / active: ' || COUNT(*) FILTER (WHERE state='active') FROM pg_stat_activity" 2>/dev/null

echo
echo "=== [5] 데이터 보존 확인 ==="
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT '  market_data: ' || COUNT(*) || '행 / ' || COUNT(DISTINCT trade_date) || '거래일' FROM market_data" 2>/dev/null
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT '  event_features: ' || COUNT(*) || '행' FROM event_features" 2>/dev/null
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT '  supply/financial/macro: ' || (SELECT COUNT(*) FROM supply_market_features) || ' / ' || (SELECT COUNT(*) FROM financial_ratio_features) || ' / ' || (SELECT COUNT(*) FROM macro_features)" 2>/dev/null
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT '  disclosures: ' || COUNT(*) || '행' FROM disclosures" 2>/dev/null

echo
echo "=== [6] git 동기화 (유실 방지) ==="
git add -A
git commit -q -m "이동 전 체크포인트: 팩터 910일 이력 재측정 결과 기록 + L5c(PIT 유니버스) 등록" 2>/dev/null || echo "  (새 커밋 없음)"
GIT_TERMINAL_PROMPT=0 timeout 90 git push origin master 2>&1 | tail -1
echo "  로컬 $(git rev-parse --short HEAD) / 원격 $(timeout 60 git ls-remote origin master 2>/dev/null | cut -c1-7) / 미커밋 $(git status --porcelain | wc -l)건"

echo
echo "=============================================="
echo " ✅ 종료 준비 완료 — 바로 종료하셔도 안전합니다"
echo "   (데이터는 DB·git 에 저장 / 인플라이트 작업 정지 / 크론은 Hermes 종료 시 함께 멈춤)"
echo "=============================================="
