#!/usr/bin/env bash
# refresh_feature_sources — 원천→피처 테이블 적재(build_*)를 신선도 게이트로 감싼 드라이버.
#
# WHY (2026-11-09 실측): supply_market_features / macro_features / financial_ratio_features 는
# 각 build_*.py 전용 테이블인데 **어느 크론·파이프라인에도 배선돼 있지 않다**
#   grep -nE "build_(supply|macro|financial|event)" scripts/full_pipeline_dd.sh → 0건
#   grep -rl build_supply_market_features /home/jhshi/cron /etc/cron.d/analyist_dd → 0건
# → 누군가 손으로 돌릴 때만 전진한다. 실제로 2026-10-09~11-06(21거래일) 호스트 정지 후
#   market_data(KIS·yfinance)는 11-09 까지 자동 복구됐지만 위 3개 테이블은 10-08 에 멈춰 있었다.
#   신선도 게이트는 이를 CRITICAL 로 잡았으나 '감지 전용'이라 아무것도 고치지 않는다.
#
# 사용:
#   bash scripts/refresh_feature_sources.sh --check     # 적재 없이 신선도만 판정 (rc 0=전부 신선, 4=stale)
#   bash scripts/refresh_feature_sources.sh             # stale 한 것만 순서대로 재적재
#   DRY=1 bash scripts/refresh_feature_sources.sh       # 무엇을 돌릴지만 출력
#
# 규율:
#  · 직렬 실행(CPU 4코어). 빌더는 모두 멱등(DELETE+COPY) 이므로 재실행 안전.
#  · **원천이 stale 이면 그 빌더는 건너뛴다** — 원천 없이 돌리면 갭 구간에 NULL/0 을 적재해
#    '정보 없음'을 0 으로 위장한다(학습 오염). 예: supply_market 의 원천은 market_data(신선)뿐
#    아니라 foreign_institutional(수급)·krx_short_selling 도 함께 봐야 한다.
#  · 피처가 원천보다 신선할 수는 없다: 판정은 (원천 max, 피처 max) 쌍으로 한다.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
PY="${PY:-/usr/bin/python3}"
MODE="build"; [ "${1:-}" = "--check" ] && MODE="check"

q() { docker exec stock_postgres psql -U stock_user -d stock_trading -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }

MARKET_MAX=$(q "select max(trade_date) from market_data")
echo "기준 거래일(market_data max) = ${MARKET_MAX}"

# name | 피처테이블 | 빌더 | 원천 spec(테이블:날짜컬럼[:quarterly])
SPECS=(
  "macro|macro_features|scripts/build_macro_features.py|macro_indicators:date"
  "financial_ratio|financial_ratio_features|scripts/build_financial_ratio_features.py|financial_statements:report_date:quarterly"
  "supply_market|supply_market_features|scripts/build_supply_market_features.py|market_data:trade_date,foreign_institutional:trade_date,krx_short_selling:trade_date"
)

STALE=(); READY=(); LINE=""
for spec in "${SPECS[@]}"; do
  IFS='|' read -r name feat builder srcs <<<"$spec"
  f_max=$(q "select max(trade_date) from ${feat}")
  src_desc=""; src_stale=""
  IFS=',' read -ra PAIRS <<<"$srcs"
  for p in "${PAIRS[@]}"; do
    IFS=':' read -r t c kind <<<"$p"
    v=$(q "select max(${c}) from ${t}")
    src_desc+="${t##*_}=${v} "
    # 분기 보고서(report_date)는 거래일 격자와 다르다 — as-of 조인이 설계상 흡수하므로
    # 원천이 최신 거래일과 다르다는 이유로 적재를 막지 않는다(막으면 신선도가 영구 STALE).
    if [ "${kind:-}" = "quarterly" ]; then
      :
    else
      [ "$v" = "$MARKET_MAX" ] || src_stale+="${t} "
    fi
  done
  verdict="STALE"; [ -z "$src_stale" ] && [ "$f_max" = "$MARKET_MAX" ] && verdict="FRESH"
  printf '  %-16s 피처 %-12s 원천 %s %s\n' "$name" "$f_max" "$src_desc" "$verdict"
  LINE+="${name}:${f_max} "
  if [ "$verdict" = "STALE" ]; then
    STALE+=("$name")
    if [ -z "$src_stale" ]; then READY+=("$name|$builder"); fi
    [ -n "$src_stale" ] && echo "    ↳ 원천 미복구로 적재 보류: ${src_stale}"
  fi
done

echo "요약: ${LINE% }"
if [ "$MODE" = "check" ]; then
  if [ ${#STALE[@]} -eq 0 ]; then echo "RESULT: ALL_FRESH"; exit 0; fi
  echo "RESULT: STALE=${STALE[*]} READY=${READY[*]:-none}"; exit 4
fi
if [ ${#READY[@]} -eq 0 ]; then echo "적재 가능한 항목 없음(원천 미복구)"; exit 4; fi

rc_all=0
for b in "${READY[@]}"; do
  IFS='|' read -r name builder <<<"$b"
  echo "=== $name : $builder"
  if [ -n "${DRY:-}" ]; then echo "  (DRY) skip"; continue; fi
  start=$(date +%s); timeout 3600 "$PY" "$builder"; rc=$?
  echo "  rc=$rc elapsed=$(( $(date +%s) - start ))s"
  [ $rc -ne 0 ] && rc_all=1
done
exit $rc_all
