#!/bin/bash
# post_close_checks.sh — 장 마감 후 남은 점검을 **순차적으로** 돌리고 결과를 한 파일에 남긴다.
#
# WHY (2026-11-23 사용자 지시 "장 끝나면 남은 작업들 순차적으로 진행"):
#   남은 감시 항목(수급 지연·시장패턴 원장·일봉 공백·yfinance 자기신고·발행 관문)이
#   각각 다른 스크립트로 흩어져 있어, 장 마감 후 매번 사람이 골라 돌려야 했다.
#   이 래퍼가 정해진 순서로 실행하고 rc·마지막 판정 줄을 JSON 으로 요약한다.
#   판정 실패가 다른 점검을 막지 않는다(set +e) — 한 항목 오류가 전체를 침묵시키면 안 된다.
#
# 산출물:
#   reports/post_close_checks_latest.json   (최신 요약 — 기계 판독)
#   reports/post_close_checks_<YYYYMMDD>.log (전문 로그)
#
# 사용: post_close_checks.sh [--quiet]
set +e
REPO=/home/jhshi/analyist_dd
cd "$REPO" || exit 1
[ -f .env ] && { set -a; . ./.env; set +a; }
# .env 는 컨테이너 내부 기준(POSTGRES_HOST=postgres, 5432)이다 — 호스트에서는 매핑 포트로.
# 2026-11-23 실측: 이 덮어쓰기를 빼먹어 r21·market_pattern 이 'postgres 호스트명 실패'로 죽었다.
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5434
PY=/usr/bin/python3
DAY=$(date +%Y%m%d)
LOG="reports/post_close_checks_${DAY}.log"
OUT="reports/post_close_checks_latest.json"
: > "$LOG"

run_check() {  # $1=키  $2=타임아웃(초)  $3...=명령
    local key="$1" timeout_s="$2"; shift 2
    local started ended rc tail
    started=$(date +%s)
    echo "=== [$(date '+%F %T')] $key: $* ===" >> "$LOG"
    timeout "$timeout_s" "$@" >> "$LOG" 2>&1
    rc=$?
    ended=$(date +%s)
    tail=$(grep -v '^\s*$' "$LOG" | tail -n 1 | tr -d '\r' | sed 's/"/\\"/g' | head -c 300)
    printf '%s\t%s\t%s\t%s\n' "$key" "$rc" "$((ended - started))" "$tail" >> /tmp/post_close_results.tsv
    return 0
}

: > /tmp/post_close_results.tsv
echo "[$(date '+%F %T')] post-close checks 시작 (기준일 $(date +%F))" | tee -a "$LOG"

# 1) 발행 관문 G1~G5 (가장 중요 — 피드·후보·메타·일봉·signal_date)
run_check "pipeline_gates" 300 "$PY" scripts/verify_daily_pipeline_gates.py
# 2) 수급 최신일 지연 프로브 (R21, 읽기 전용)
run_check "supply_lag_r21" 180 "$PY" scripts/r21_supply_lag_probe.py
# 3) 시장패턴 원장 커버리지 (R41, 읽기 전용)
run_check "market_pattern_r41" 180 "$PY" scripts/market_pattern_coverage_check.py
# 4) yfinance 컨테이너 phase 자기신고 (R28 판정)
run_check "yf_claim_r28" 180 "$PY" scripts/r28_yf_claim_check.py
# 5) 일봉 공백 감시 (R35) — data_gap 은 인자 없이 돌면 자체 판정한다
run_check "data_gap_r35" 900 "$PY" scripts/data_gap.py check

$PY - "$OUT" "$DAY" <<'EOF' | tee -a "$LOG"
import json, sys, datetime
out, day = sys.argv[1], sys.argv[2]
rows = []
for line in open('/tmp/post_close_results.tsv', encoding='utf-8'):
    parts = line.rstrip('\n').split('\t')
    if len(parts) < 4:
        continue
    rows.append({"check": parts[0], "rc": int(parts[1]), "seconds": int(parts[2]),
                 "last_line": parts[3]})
payload = {
    "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "date": day,
    "checks": rows,
    "failed": [r["check"] for r in rows if r["rc"] != 0],
}
json.dump(payload, open(out, "w"), ensure_ascii=False, indent=2)
print("요약:", ", ".join("{0}:rc={1}".format(r["check"], r["rc"]) for r in rows))
print("실패:", payload["failed"] or "없음")
EOF
echo "[$(date '+%F %T')] done → $OUT" | tee -a "$LOG"
exit 0
