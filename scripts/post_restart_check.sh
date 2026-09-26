#!/usr/bin/env bash
# 재시작 후 전체 상태 검증 (컴퓨터 이동 후 복귀 시 1회 실행).
#
# 사용: bash /home/jhshi/analyist_dd/scripts/post_restart_check.sh
set -uo pipefail
cd /home/jhshi/analyist_dd || { echo "✗ 프로젝트 경로 없음"; exit 1; }
PY=/mnt/c/Users/jhshi/Python312-64/python.exe
OK=0; WARN=0; FAIL=0
ok(){ echo "  ✅ $1"; OK=$((OK+1)); }
warn(){ echo "  ⚠️  $1"; WARN=$((WARN+1)); }
fail(){ echo "  ❌ $1"; FAIL=$((FAIL+1)); }

echo "=============================================="
echo " 재시작 검증  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

echo
echo "[1] Docker 스택 (기대: 17개 컨테이너)"
N=$(docker ps -q 2>/dev/null | wc -l)
if [ "$N" -ge 15 ]; then ok "컨테이너 $N개 실행 중"; else
  fail "컨테이너 $N개 (부족) → 다음 실행: cd /home/jhshi/analyist_dd && docker compose up -d --no-build"
fi
for c in stock_postgres stock_xgboost_ml stock_job_runner stock_strategy_agents; do
  S=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)
  [ "$S" = "running" ] && ok "$c running" || fail "$c 상태: ${S:-없음}"
done

echo
echo "[2] DB 데이터 보존 (백필 결과)"
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT 'market_data ' || COUNT(*) || '행 / ' || COUNT(DISTINCT trade_date) || '거래일 / 최초 ' || MIN(trade_date) FROM market_data" 2>/dev/null \
  | sed 's/^/  /' || fail "DB 조회 실패"
MD=$(docker exec stock_postgres psql -U stock_user -d stock_trading -tAc "SELECT COUNT(DISTINCT trade_date) FROM market_data" 2>/dev/null | tr -d ' \r')
if [ "${MD:-0}" -ge 900 ] 2>/dev/null; then ok "거래일 ${MD}일 (백필 보존 ✓)"; else warn "거래일 ${MD}일 (910 기대)"; fi

echo
echo "[3] git 동기화"
echo "  로컬 $(git rev-parse --short HEAD) / 미커밋 $(git status --porcelain | wc -l)건"
GIT_TERMINAL_PROMPT=0 timeout 60 git fetch -q origin 2>/dev/null
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master 2>/dev/null)" ]; then ok "원격과 동기화 ✓"; else warn "원격과 차이 있음 (pull 필요할 수 있음)"; fi

echo
echo "[4] Hermes 크론 (자율 루프)"
for j in system-hygiene alert-dispatch quant-model-engineer-overnight quant-researcher-monitor; do
  echo "  - $j: 설정 파일 $(ls ~/.hermes/cron/*.json >/dev/null 2>&1 && echo 존재 || echo 확인필요)"
done
echo "  (크론 목록·다음 실행은 Hermes 도구로 확인: cronjob_manage list)"

echo
echo "[5] 브리지 / Creon 로그인 (사람 단계 후 확인)"
if [ -x "$PY" ]; then
  OUT=$("$PY" -c "
import urllib.request, json
try:
    with urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=8) as r:
        d = json.loads(r.read().decode())
    print('connected=' + str(d.get('connected')))
except Exception as e:
    print('error=' + type(e).__name__)
" 2>/dev/null | tr -d '\r')
  case "$OUT" in
    connected=True) ok "브리지 연결 ✓ (크레온 로그인 유지)" ;;
    connected=False) warn "브리지 응답했지만 미연결 → 크레온 PLUS 로그인 필요" ;;
    *) fail "브리지 응답 없음 → bridge_run_admin.bat 실행 필요 (관리자 콘솔)" ;;
  esac
else
  warn "Windows 파이썬 경로 확인 필요 ($PY)"
fi

echo
echo "[6] 트레이딩 루프 (월요일 09:00 전 필요)"
if "$PY" -c "import sys; sys.exit(0)" 2>/dev/null; then
  LOOP=$(/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command \
    "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*run_market_loop*' } | Measure-Object).Count" 2>/dev/null | tr -d ' \r')
  [ "${LOOP:-0}" -ge 1 ] 2>/dev/null && ok "market loop 실행 중" \
    || warn "market loop 미실행 → loop_start_bg.bat (관리자 콘솔, 월요일 09:00 전)"
fi

echo
echo "[7] 다음 자동 작업 대기열"
/usr/bin/python3 - <<'PYEOF' 2>/dev/null
import json, os
for f, label in (("docs/QUANT_MODEL_BACKLOG.json", "모델"), ("docs/QUANT_RESEARCH_BACKLOG.json", "리서처")):
    try:
        b = json.load(open(f))
    except Exception:
        continue
    pend = [(i["id"], i.get("priority"), i["title"][:44]) for i in b["items"]
            if i.get("status") in ("pending", "needs_setup", "in_progress")]
    pend.sort(key=lambda x: (x[1] or 99))
    print(f"  [{label}] 대기 {len(pend)}건 — 상위:")
    for pid, p, title in pend[:3]:
        print(f"     p{p} {pid:5} {title}")
PYEOF

echo
echo "=============================================="
echo " 결과: 정상 $OK / 주의 $WARN / 실패 $FAIL"
[ "$FAIL" -eq 0 ] && echo " → 복귀 완료. 남은 사람 단계: 브리지·루프(월요일 09:00 전)" \
                  || echo " → 실패 항목의 안내 명령을 실행한 뒤 재검증하세요"
echo "=============================================="
