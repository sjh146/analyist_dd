#!/usr/bin/env bash
# bootstrap_new_machine.sh — 다른 Windows(WSL) 머신에서 analyist_dd 를 클론한 뒤 1회 실행.
#
#   bash scripts/bootstrap_new_machine.sh --check        # 점검만(변경 없음) — 먼저 이걸 돌려라
#   bash scripts/bootstrap_new_machine.sh                # .env 준비 + 스택 기동 + 검증
#   bash scripts/bootstrap_new_machine.sh --with-cron    # + 운영 크론 설치(루트 권한 필요)
#
# 설계 원칙
#  - **멱등**: 여러 번 돌려도 안전하다(이미 있으면 건너뛴다).
#  - **거짓 성공 금지**: 각 단계는 실제 확인(health/카운트/SQL)으로 검증한다.
#  - **비밀 취급**: .env 는 만들기만 하고 값을 출력하지 않는다. 플레이스홀더가 남아 있으면 중단한다.
set -uo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJ"
CHECK_ONLY=0
WITH_CRON=0
FORCE=0
for a in "$@"; do
  case "$a" in
    --check) CHECK_ONLY=1 ;;
    --with-cron) WITH_CRON=1 ;;
    --force) FORCE=1 ;;
    *) echo "알 수 없는 옵션: $a"; exit 2 ;;
  esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
step() { printf '\n\033[1m[%s]\033[0m %s\n' "$1" "$2"; }

FAIL=0

# ── 1. 환경 점검 ────────────────────────────────────────────────────────────
step 1/6 "실행 환경 점검"
if grep -qi microsoft /proc/version 2>/dev/null; then ok "WSL 확인"; else warn "WSL 이 아닌 것 같다(리눅스면 그대로 진행 가능)"; fi
if command -v docker >/dev/null; then ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"; else bad "docker 없음 — 설치 필요(wsl --install 또는 Docker Desktop)"; FAIL=1; fi
if docker compose version >/dev/null 2>&1; then ok "docker compose $(docker compose version --short 2>/dev/null)"; else bad "docker compose 없음"; FAIL=1; fi
if docker info >/dev/null 2>&1; then ok "docker 데몬 동작"; else bad "docker 데몬 미동작 — 'sudo service docker start' 또는 Docker Desktop 실행"; FAIL=1; fi
if /usr/bin/python3 -c "import psycopg2" 2>/dev/null; then ok "/usr/bin/python3 + psycopg2"; else bad "/usr/bin/python3 에 psycopg2 없음 — sudo apt-get install -y python3-psycopg2"; FAIL=1; fi
command -v git >/dev/null && ok "git" || { bad "git 없음"; FAIL=1; }
command -v curl >/dev/null && ok "curl" || warn "curl 없음(Prometheus 검증에 필요)"

# ── 2. 경로 호환 ────────────────────────────────────────────────────────────
step 2/6 "경로 호환 (스크립트·크론이 /home/jhshi/analyist_dd 를 전제로 작성돼 있음)"
EXPECTED="/home/jhshi/analyist_dd"
if [ "$PROJ" = "$EXPECTED" ]; then
  ok "경로 일치: $PROJ"
else
  warn "실제 경로: $PROJ  (기대: $EXPECTED)"
  if [ -L "$EXPECTED" ] || [ -d "$EXPECTED" ]; then
    ok "$EXPECTED 이미 존재 — 스크립트가 그대로 동작한다"
  elif [ ! "$CHECK_ONLY" = 1 ]; then
    if mkdir -p "$(dirname "$EXPECTED")" 2>/dev/null && ln -sfn "$PROJ" "$EXPECTED" 2>/dev/null; then
      ok "심볼릭 링크 생성: $EXPECTED -> $PROJ"
    else
      warn "심볼릭 링크 생성 권한 없음. 다음 중 하나를 하라:"
      echo "        (a) 관리자 셸에서: sudo ln -sfn $PROJ $EXPECTED"
      echo "        (b) 스크립트 호출 시 PROJ_DIR=$PROJ 를 export (크론 프롬프트/명령에 반영)"
    fi
  else
    warn "--check 모드: 링크를 만들지 않았다"
  fi
fi

# ── 3. .env 준비 ───────────────────────────────────────────────────────────
step 3/6 ".env 준비"
if [ ! -f .env ]; then
  if [ "$CHECK_ONLY" = 1 ]; then
    warn ".env 없음 — 실제 실행 시 .env.example 에서 생성한다"
  else
    cp .env.example .env && ok ".env 생성(.env.example 복사) — 값을 채워야 한다"
  fi
fi
if [ -f .env ]; then
  MISSING=""
  for k in POSTGRES_PASSWORD INTERNAL_API_KEY API_GATEWAY_KEY; do
    v="$(grep -E "^${k}=" .env | head -1 | cut -d= -f2-)"
    [ -z "$v" ] || echo "$v" | grep -qE '^(your_|change-me|)$' && MISSING="$MISSING $k"
  done
  for k in KIS_APP_KEY KIS_APP_SECRET; do
    v="$(grep -E "^${k}=" .env | head -1 | cut -d= -f2-)"
    [ -z "$v" ] || echo "$v" | grep -qE '^(your_|change-me|)$' && MISSING="$MISSING $k(시세·수급 수집)"
  done
  # KIS_ACCOUNT_NO 는 **선택**이다: 이 스택의 주문은 Creon 브리지로 나가고, KIS 는 시세·수급 조회에만
  # 쓰인다(실측: .env 에서 비어 있고 코드에서도 config.py 선언 후 미사용). 필수로 잡으면 오탐이 된다.
  vacct="$(grep -E '^KIS_ACCOUNT_NO=' .env | head -1 | cut -d= -f2-)"
  if [ -z "$vacct" ] || echo "$vacct" | grep -qE '^(your_|change-me|)$'; then
    echo "  · KIS_ACCOUNT_NO 미설정 — KIS 주문 API 를 쓸 때만 필요(이 스택은 Creon 브리지 주문이므로 무방)"
  fi
  for k in DART_API_KEY ECOS_API_KEY KRX_API_KEY; do
    v="$(grep -E "^${k}=" .env | head -1 | cut -d= -f2-)"
    [ -z "$v" ] || echo "$v" | grep -qE '^(your_|change-me|)$' && MISSING="$MISSING $k(수집)"
  done
  if [ -n "$MISSING" ]; then
    bad "플레이스홀더가 남아 있음:$MISSING"
    echo "        → .env 를 열어 실제 키를 넣어라(키는 출력·기록하지 않는다)."
    [ "$FORCE" = 1 ] || { [ "$CHECK_ONLY" = 1 ] || FAIL=1; }
  else
    ok ".env 필수 키가 모두 채워져 있다"
  fi
fi

# ── 4. 스택 기동 ───────────────────────────────────────────────────────────
step 4/6 "컨테이너 기동"
if [ "$CHECK_ONLY" = 1 ]; then
  warn "--check 모드: 기동 생략. 기동 명령: docker compose up -d"
elif [ "$FAIL" = 1 ]; then
  bad "앞 단계 실패로 기동을 건너뛴다(--force 로 무시 가능)"
else
  docker compose up -d 2>&1 | tail -5
  printf '  postgres 준비 대기'
  for i in $(seq 1 40); do
    if docker exec stock_postgres pg_isready -U "${POSTGRES_USER:-stock_user}" >/dev/null 2>&1; then printf ' 대기완료\n'; break; fi
    printf '.'; sleep 3
  done
  docker exec stock_postgres pg_isready -U "${POSTGRES_USER:-stock_user}" >/dev/null 2>&1 \
    && ok "postgres 준비 완료" || { bad "postgres 미준비 — docker logs stock_postgres 확인"; FAIL=1; }
fi

# ── 5. 검증 ────────────────────────────────────────────────────────────────
step 5/6 "실제 동작 검증"
if [ "$CHECK_ONLY" = 1 ]; then
  warn "--check 모드: 검증 생략"
else
  n=$(docker ps --format '{{.Names}}' | wc -l)
  echo "  실행 중 컨테이너: ${n}개"
  [ "$n" -ge 10 ] && ok "컨테이너 기동 확인" || { warn "컨테이너가 ${n}개뿐 — docker compose ps 로 확인"; }

  TABLES=$(docker exec stock_postgres psql -U "${POSTGRES_USER:-stock_user}" -d "${POSTGRES_DB:-stock_trading}" -tAc \
           "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null | tr -d ' ')
  if [ -n "$TABLES" ] && [ "$TABLES" -gt 20 ] 2>/dev/null; then
    ok "DB 스키마 초기화됨 (테이블 ${TABLES}개)"
  else
    bad "DB 테이블이 ${TABLES:-0}개 — init-scripts 가 실행되지 않았다. 볼륨을 지우고 재기동: docker compose down -v && docker compose up -d"
    FAIL=1
  fi

  ROWS=$(docker exec stock_postgres psql -U "${POSTGRES_USER:-stock_user}" -d "${POSTGRES_DB:-stock_trading}" -tAc \
         "SELECT COUNT(*) FROM market_data" 2>/dev/null | tr -d ' ')
  echo "  market_data 행수: ${ROWS:-0}"
  if [ "${ROWS:-0}" -lt 1000 ] 2>/dev/null; then
    warn "데이터가 거의 없다 → DB 시딩 필요(아래 6단계)"
  else
    ok "일봉 데이터 적재됨"
  fi

  if curl -s --max-time 8 http://127.0.0.1:9090/-/ready >/dev/null 2>&1; then
    UP=$(curl -s --max-time 8 'http://127.0.0.1:9090/api/v1/targets?state=active' 2>/dev/null \
         | grep -o '"health":"up"' | wc -l)
    ok "Prometheus 응답 (up 타겟 ${UP}개)"
  else
    warn "Prometheus 미응답 — docker logs analyist_dd-prometheus-1"
  fi

  DQ=$(curl -s --max-time 8 'http://127.0.0.1:9090/api/v1/query?query=count(%7B__name__%3D~%22dq_.%2A%22%7D)' 2>/dev/null \
       | grep -o '"value":\[[^]]*\]' | grep -o '"[0-9]*"' | tail -1 | tr -d '"')
  [ -n "${DQ:-}" ] && [ "${DQ:-0}" -gt 0 ] 2>/dev/null && ok "DQ 메트릭 ${DQ}개 노출" || warn "DQ 메트릭 미노출(컨테이너 초기 기동 직후면 정상 — 잠시 후 재확인)"
fi

# ── 6. 크론/다음 단계 ──────────────────────────────────────────────────────
step 6/6 "크론 및 다음 단계"
if [ "$WITH_CRON" = 1 ] && [ "$CHECK_ONLY" = 0 ]; then
  SRC="$PROJ/home_cron/analyist_dd.cron"
  [ -f "$SRC" ] || SRC="$HOME/cron/analyist_dd.cron"
  if [ -f "$SRC" ]; then
    mkdir -p "$HOME/cron" && cp "$SRC" "$HOME/cron/analyist_dd.cron"
    if sudo -n true 2>/dev/null; then
      sudo cp "$SRC" /etc/cron.d/analyist_dd && ok "크론 설치(/etc/cron.d/analyist_dd)"
    elif command -v wsl.exe >/dev/null 2>&1; then
      wsl.exe -u root -- cp "$SRC" /etc/cron.d/analyist_dd 2>/dev/null && ok "크론 설치(root 경유)" \
        || warn "크론 설치 실패 — 관리자 콘솔에서 직접: cp $SRC /etc/cron.d/analyist_dd"
    else
      warn "루트 권한 없음 — 관리자 콘솔에서: sudo cp $SRC /etc/cron.d/analyist_dd"
    fi
  else
    warn "크론 원본($SRC)을 찾지 못했다"
  fi
else
  echo "  운영 크론 설치: bash scripts/bootstrap_new_machine.sh --with-cron"
fi

cat <<'EOS'
  데이터 시딩(새 DB 는 비어 있다) — 둘 중 하나:
    (a) 기존 머신에서:  bash scripts/db_dump.sh      → dumps/*.sql.gz 를 새 머신으로 복사
        새 머신에서:    bash scripts/db_restore.sh dumps/analyist_dd_YYYYMMDD.sql.gz
    (b) 새로 수집:      로그인/키 준비 후 수집 러너를 순차 실행(수 시간~수일)
  자세한 절차: docs/SETUP_NEW_MACHINE.md
EOS

printf '\n'
if [ "$FAIL" = 1 ]; then
  printf '\033[31m결과: 실패 항목이 있다 — 위 ✗ 를 해결하고 다시 실행하라.\033[0m\n'; exit 1
fi
printf '\033[32m결과: 통과.\033[0m\n'
