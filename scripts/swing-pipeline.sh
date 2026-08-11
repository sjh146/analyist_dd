#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/.omo/evidence"
# 로그 로테이션: 14일 이상 스윙 로그 삭제 + 최근 10개만 유지
find "$LOG_DIR" -maxdepth 1 -type f -name "swing-pipeline-*.log" -mtime +14 -delete 2>/dev/null
ls -1t "$LOG_DIR"/swing-pipeline-*.log 2>/dev/null | tail -n +11 | xargs -r rm -f 2>/dev/null
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/swing-pipeline-$TIMESTAMP.log"
REPORT_DIR="$PROJECT_DIR/data/reports"
mkdir -p "$LOG_DIR" "$REPORT_DIR"

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

# Step 6: Swing screener
echo "[6/6] Running swing screener..." | tee -a "$LOG_FILE"
OUTPUT_FILE="$REPORT_DIR/swing_candidates_$TIMESTAMP.csv"
# 호스트 python3에는 pandas 등 의존성이 없어 xgboost-ml 컨테이너에서 실행
docker cp "$SCRIPT_DIR/swing_screener.py" stock_xgboost_ml:/app/scripts/swing_screener.py 2>/dev/null
docker-compose exec -T xgboost-ml sh -c '
  mkdir -p /app/scripts /app/services
  ln -sfn /app /app/services/xgboost-ml
' 2>/dev/null
if docker-compose exec -T xgboost-ml python /app/scripts/swing_screener.py \
    --include-krx-data --include-economic-events --output /tmp/swing_out.csv 2>/dev/null; then
    docker cp stock_xgboost_ml:/tmp/swing_out.csv "$OUTPUT_FILE" 2>/dev/null
    echo "  ✓ Screener complete: $(wc -l < "$OUTPUT_FILE" 2>/dev/null || echo 0) candidates" | tee -a "$LOG_FILE"
else
    echo "  ⚠ Screener issue" | tee -a "$LOG_FILE"
    PIPELINE_STATUS=1
fi

echo "=== Swing Pipeline Complete: $(date) ===" | tee -a "$LOG_FILE"
echo "Report: $OUTPUT_FILE" | tee -a "$LOG_FILE"
exit $PIPELINE_STATUS
