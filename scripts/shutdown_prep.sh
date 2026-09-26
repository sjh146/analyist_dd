#!/usr/bin/env bash
# 컴퓨터 이동 전 정리 (자기매칭 없이 — 브래킷 패턴 사용).
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1

echo "=== 1) 실제 실행 중인 작업 (브래킷 패턴으로 자기매칭 회피) ==="
for pat in 'run_factor_job' 'model_engineer_cycle\.py --run' 'researcher_cycle\.py --run' 'naver_daily_backfill' 'wf_label_sweep' 'ask_claude.sh' 'opencode'; do
  PIDS=$(pgrep -f "[${pat:0:1}]${pat:1}" 2>/dev/null | tr '\n' ' ')
  if [ -n "$PIDS" ]; then
    echo "  $pat → pid: $PIDS"
    for p in $PIDS; do
      CMD=$(ps -o cmd= -p "$p" 2>/dev/null | cut -c1-70)
      echo "      $p: $CMD"
    done
  fi
done

echo
echo "=== 2) 팩터·백필 등 인플라이트 작업 정지 ==="
for pat in 'run_factor_job' 'naver_daily_backfill'; do
  PIDS=$(pgrep -f "[${pat:0:1}]${pat:1}" 2>/dev/null | tr '\n' ' ')
  for p in $PIDS; do
    kill "$p" 2>/dev/null && echo "  중단 요청: $p ($pat)" || true
  done
done
sleep 3
LEFT=$(pgrep -f '[r]un_factor_job|[n]aver_daily_backfill' 2>/dev/null | wc -l)
echo "  남은 인플라이트: ${LEFT}개"

echo
echo "=== 3) docker 컨테이너 (WSL 종료 시 함께 내려감) ==="
RUNNING=$(docker ps --format '{{.Names}}' 2>/dev/null | wc -l)
echo "  실행 중 컨테이너: ${RUNNING}개"
echo "  (WSL 종료 = Docker 종료 → 재기동 시 'docker compose up -d' 필요)"

echo
echo "=== 4) DB 무결성 (미완 트랜잭션) ==="
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT '  idle-in-transaction: ' || COUNT(*) FILTER (WHERE state='idle in transaction') || ' / active: ' || COUNT(*) FILTER (WHERE state='active') FROM pg_stat_activity" 2>/dev/null

echo
echo "=== 5) git 동기화 (작업 유실 방지) ==="
git add -A
git commit -q -m "R17 완료 기록(910거래일) + U3(확장 이력 패널 재빌드) 등록" 2>/dev/null || echo "  (커밋할 변경 없음)"
GIT_TERMINAL_PROMPT=0 timeout 90 git push origin master 2>&1 | tail -1
echo "  로컬 $(git rev-parse --short HEAD) / 원격 $(timeout 60 git ls-remote origin master 2>/dev/null | cut -c1-7)"
echo "  미커밋 변경: $(git status --porcelain | wc -l)개"

echo
echo "=== 6) 백필 결과 (DB 영구 저장 확인) ==="
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT '  market_data: ' || COUNT(*) || '행 / 최초 ' || MIN(trade_date) || ' / 거래일 ' || COUNT(DISTINCT trade_date) FROM market_data" 2>/dev/null
