#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/.omo/evidence"
# 로그 디렉터리를 **먼저** 만든다. (없으면 아래 find -delete 가 exit 1 → set -e 로
# 스크립트가 즉시 종료해 08:30 스윙 크론이 아무 일도 안 하고 끝난다. 실측 2026-09-24)
mkdir -p "$LOG_DIR" "$PROJECT_DIR/data/reports"
# 로그 로테이션: 14일 이상 스윙 로그 삭제 + 최근 10개만 유지
if [ -d "$LOG_DIR" ]; then
    find "$LOG_DIR" -maxdepth 1 -type f -name "swing-pipeline-*.log" -mtime +14 -delete 2>/dev/null || true
    ls -1t "$LOG_DIR"/swing-pipeline-*.log 2>/dev/null | tail -n +11 | xargs -r rm -f 2>/dev/null || true
fi
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/swing-pipeline-$TIMESTAMP.log"
REPORT_DIR="$PROJECT_DIR/data/reports"

echo "=== Swing Pipeline Start: $(date) ===" | tee -a "$LOG_FILE"
PIPELINE_STATUS=0

# Step 1: Collect KRX data
echo "[1/6] Collecting KRX data..." | tee -a "$LOG_FILE"
if docker-compose exec -T krx-collector python -m app.main --once 2>/dev/null; then
    echo "  ✓ KRX data collected" | tee -a "$LOG_FILE"
else
    echo "  ⚠ KRX collection issue (non-critical)" | tee -a "$LOG_FILE"
fi

# Step 2: Collect US market data
echo "[2/6] Collecting US market data..." | tee -a "$LOG_FILE"
if docker-compose exec -T yfinance-collector python -c "
import yfinance as yf, pandas as pd, os
tickers = {'NASDAQ':'^IXIC','SOX':'^SOX','SP500':'^GSPC','VIX':'^VIX','USDKRW':'USDKRW=X','KOSPI200_NIGHT':'KOSPI200.KS'}
rows = []
for name, sym in tickers.items():
    try:
        h = yf.Ticker(sym).history(period='5d')
        if not h.empty:
            rows.append({'trade_date':str(pd.Timestamp.now().date()),'index_name':name,'close_price':float(h.iloc[-1]['Close'])})
    except: pass
if rows:
    print(f'Collected {len(rows)} US data points')
" 2>/dev/null; then
    echo "  ✓ US market data collected" | tee -a "$LOG_FILE"
else
    echo "  ⚠ US data issue (non-critical)" | tee -a "$LOG_FILE"
fi

# Step 3: Update economic calendar
echo "[3/6] Updating economic calendar..." | tee -a "$LOG_FILE"
if docker-compose exec -T economic-calendar python -m app.main --once 2>/dev/null; then
    echo "  ✓ Calendar updated" | tee -a "$LOG_FILE"
else
    echo "  ⚠ Calendar issue (non-critical)" | tee -a "$LOG_FILE"
fi

# Step 4: Feature engineering
echo "[4/6] Running feature engineering..." | tee -a "$LOG_FILE"
# feature_pipeline.py는 __main__ 진입점이 없어 -m 실행 불가 — 스크리너가 직접 사용하므로 스킵
echo "  ⚠ Feature pipeline skipped (screener가 직접 계산)" | tee -a "$LOG_FILE"

# Step 5: ML inference
echo "[5/6] Running ML inference..." | tee -a "$LOG_FILE"
if docker-compose exec -T xgboost-ml python -m app.inference.predictor --all-stocks 2>/dev/null; then
    echo "  ✓ ML inference complete" | tee -a "$LOG_FILE"
else
    echo "  ⚠ ML inference issue (model may not be trained)" | tee -a "$LOG_FILE"
fi

# Step 6: Swing screener — job-runner 컨테이너에서 실행한다.
# (이전 구현은 `docker-compose exec` 로 스크리너를 돌리고 CSV 만 복사했는데,
#  ① compose 호출이 실패하면 set -e 로 스크립트가 그 자리에서 죽고(실측 exit 1)
#  ② 정작 피드가 읽는 reports/swing_latest.json 이 갱신되지 않았다.
#  job-runner 의 run_swing_job.py 는 스크리너 실행 + swing_latest.json 기록을 모두 한다.)
echo "[6/6] Running swing screener (job-runner)..." | tee -a "$LOG_FILE"
OUTPUT_FILE="$REPORT_DIR/swing_candidates_$TIMESTAMP.csv"
if docker exec stock_job_runner sh -c \
     'cd /app && REPORTS_DIR=/app/reports python /app/app/scripts/run_swing_job.py' \
     >> "$LOG_FILE" 2>&1; then
    cp -f "$PROJECT_DIR/reports/swing_latest.json" "$OUTPUT_FILE" 2>/dev/null || true
    echo "  ✓ Screener complete: reports/swing_latest.json 갱신" | tee -a "$LOG_FILE"
else
    echo "  ⚠ Screener issue" | tee -a "$LOG_FILE"
    PIPELINE_STATUS=1
fi

echo "=== Swing Pipeline Complete: $(date) ===" | tee -a "$LOG_FILE"
echo "Report: $OUTPUT_FILE" | tee -a "$LOG_FILE"
exit $PIPELINE_STATUS
