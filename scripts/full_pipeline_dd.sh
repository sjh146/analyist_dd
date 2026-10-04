#!/bin/bash
# full_pipeline_dd.sh — End-to-end auto pipeline (DD variant)
set +e
cd "$(dirname "$0")/.."
LOG_DIR="reports"
mkdir -p "$LOG_DIR"

# ── 로그 로테이션: 실행 전 오래된/초과 로그 자동 정리 ──────────────
# 1) 14일 이상 된 파이프라인 로그 삭제
find "$LOG_DIR" -maxdepth 1 -type f -name "full_pipeline_dd_*.log" -mtime +14 -delete 2>/dev/null
# 2) 최근 15개만 유지 (초과분 삭제)
ls -1t "$LOG_DIR"/full_pipeline_dd_*.log 2>/dev/null | tail -n +16 | xargs -r rm -f 2>/dev/null
# 3) cron 로그 1MB 초과 시 최근 1000줄만 유지
for f in "$LOG_DIR"/cron_train.log "$LOG_DIR"/cron_ml_loop.log "$LOG_DIR"/news_cleanup_cron.log .omo/evidence/swing-pipeline-cron.log; do
  if [ -f "$f" ] && [ "$(stat -c%s "$f" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    tail -n 1000 "$f" > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f" 2>/dev/null
  fi
done

TIMESTAMP=$(date +%Y%m%d_%H%M)
LOG_FILE="$LOG_DIR/full_pipeline_dd_$TIMESTAMP.log"
# Log to file always; mirror to stdout ONLY when running in foreground (TTY)
if [ -t 1 ]; then
    exec > >(tee -a "$LOG_FILE") 2>&1
else
    exec >> "$LOG_FILE" 2>&1
fi

# run_docker_phase — run a python script inside a container, writing stdout to a
# plain file (O_APPEND) instead of the pipeline's stdout. High-volume output never
# blocks on a pipe buffer, and < /dev/null avoids any stdin wait.
run_docker_phase() {
    local container="$1"
    local script="$2"
    local timeout="${3:-3600}"
    local phase_log="$LOG_DIR/$(basename "$script" .py)_${TIMESTAMP}.log"
    docker exec "$container" timeout "$timeout" python3 "$script" >> "$phase_log" 2>&1 < /dev/null
    local rc=$?
    if [ -f "$phase_log" ]; then
        # 자기신고 배선(R28, 2026-10-03): 컨테이너 **내부** phase 는 scripts/dq_claim.py 가 없어
        # 러너 내부 배선(claim_start/claim_finish)이 불가능하다 → phase 가 '[claim] runner table
        # source=.. claimed=.. persisted=..' 한 줄을 찍으면 호스트가 대신 기록한다. grep 로 먼저
        # 걸러 다른 단계(claim 줄 없음)에는 비용·부작용이 없다. 헬퍼는 예외를 삼키고 exit 0 이다.
        if grep -q '^\[claim\] ' "$phase_log" 2>/dev/null; then
            /usr/bin/python3 scripts/yf_claim_from_phase_log.py "$phase_log" 2>&1 || true
        fi
        cat "$phase_log" >> "$LOG_FILE"
        rm -f "$phase_log"
    fi
    return $rc
}

# run_docker_script — like run_docker_phase, for an already-present in-container script.
run_docker_script() {
    local container="$1"
    local script="$2"
    local timeout="${3:-1800}"
    local phase_log="$LOG_DIR/$(basename "$script" .py)_${TIMESTAMP}.log"
    docker exec "$container" timeout "$timeout" python3 "$script" >> "$phase_log" 2>&1 < /dev/null
    local rc=$?
    if [ -f "$phase_log" ]; then
        cat "$phase_log" >> "$LOG_FILE"
        rm -f "$phase_log"
    fi
    return $rc
}

echo "========================================"
echo "  analyist_dd FULL PIPELINE DD"
echo "  Started: $(date)"
echo "========================================"

PROGRESS_FILE="/tmp/pipeline_phase_progress.txt"
echo "0" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 0: Container Start
# ==============================================================
echo ""
echo "=== Phase 0: Starting all containers ==="
# --no-build: 파이프라인 실행마다 이미지 빌드(CUDA 등 대용량 다운로드) 방지.
# 파이프라인 필수 서비스만 기동 (jenkins/grafana/prometheus 등은 호스트에서
# 별도 실행 중이거나 불필요 — 포트 충돌 방지).
CORE_SERVICES="postgres redis neo4j krx-collector yfinance-collector economic-calendar news-analyzer stock-vectorizer xgboost-ml strategy-agents api-gateway"

# ── 인플라이트 학습 보호 (2026-09-25 실측 구조적 충돌) ────────────────────────────
# `docker compose up -d` 는 설정 해시가 바뀌면 컨테이너를 **재생성**한다. 그 순간 컨테이너 안에서
# 돌던 장시간 작업이 SIGKILL 된다: 평일 20:00 파이프라인이 150종목 패널 빌드(254분 경과,
# 30,000/41,893 = 71.6%)를 죽였다 — 4시간 반의 작업이 통째로 사라졌다(rc=137).
# 역할 사이클(모델엔지니어/리서처)이 실행 중이면 **xgboost-ml 만** 재생성 대상에서 제외한다.
# (다른 서비스의 재생성은 그대로 — 수집기·DB 는 짧은 작업이라 영향이 작다.)
PROJ_GUARD="${PROJ_DIR:-/home/jhshi/analyist_dd}"
for PF in "$PROJ_GUARD/data/reports/me_cycle/running.pid" \
          "$PROJ_GUARD/data/reports/res_cycle/running.pid"; do
    if [ -f "$PF" ] && kill -0 "$(cat "$PF" 2>/dev/null)" 2>/dev/null; then
        echo "⚠ 역할 사이클 실행 중(pid $(cat "$PF")) → xgboost-ml 재생성 제외(인플라이트 학습 보호)"
        CORE_SERVICES="${CORE_SERVICES//xgboost-ml/}"
        break
    fi
done
docker compose up -d --no-build $CORE_SERVICES 2>&1 | tail -5

echo "Waiting for critical services (postgres, redis, neo4j)..."
for i in $(seq 1 30); do
    ALL_HEALTHY=true
    for container in stock_postgres stock_redis stock_neo4j; do
        STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null)
        if [ "$STATUS" != "healthy" ]; then
            ALL_HEALTHY=false
            break
        fi
    done
    if [ "$ALL_HEALTHY" = true ]; then
        echo "All critical services healthy."
        break
    fi
    sleep 2
done

sleep 5
docker ps --format 'table {{.Names}}\t{{.Status}}' | head -20

# ==============================================================
# PHASE 1: Data Collection
# ==============================================================
echo ""
echo "=== Phase 1: Data Collection ==="

# 1-1. yfinance: Market Data (OHLCV only — skip fundamentals to avoid rate limit)
echo "--- 1-1. yfinance: Market Data (OHLCV only) ---"
docker exec -i stock_yfinance_collector sh -c 'cat > /tmp/phase_1_1.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
from app.main import YFinanceCollectorService
from app.collectors.price_collector import PriceCollector
from app.collectors.stock_list_collector import StockListCollector
from app.processors.technical_indicators import TechnicalIndicatorCalculator
from app.processors.data_cleaner import DataCleaner
from app.storage.postgres_storage import PostgresStorage
import logging; logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logging.raiseExceptions = False

logger = logging.getLogger(__name__)
config = __import__('app.config', fromlist=['Config']).Config()
storage = PostgresStorage()
slc = StockListCollector()
stocks = slc.get_all_stocks()
logger.info(f'Total stocks to collect: {len(stocks)}')

# Upsert stock master data
for stock in stocks:
    storage.upsert_stock(stock)

# Price collection only (skip fundamentals to avoid rate limit)
pc = PriceCollector()
df = pc.collect_all(stocks)
n_recv = len(df)  # 소스(API) 수신 원시 행수 — 자기신고 source. 파서 실패를 오탐 없이 잡는 기준값.
if df.empty:
    logger.warning('No data collected')
else:
    logger.info(f'Collected data: {len(df)} rows')
    ti = TechnicalIndicatorCalculator()
    df = ti.calculate_all(df)
    dc = DataCleaner()
    df = dc.clean(df)
    import os, psycopg2
    from psycopg2.extras import execute_values
    pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password=os.environ.get('POSTGRES_PASSWORD',''))
    cur = pg.cursor()
    rows = []
    for r in df.itertuples():
        td = getattr(r, 'trade_date', None)
        if td is None:
            continue
        try:
            o, h, l, c = float(r.open), float(r.high), float(r.low), float(r.close)
            v = int(r.volume) if r.volume == r.volume else 0
        except (TypeError, ValueError):
            continue
        if any(x != x for x in (o, h, l, c)):
            continue
        rows.append((r.stock_code, td, o, h, l, c, v))
    # 시세 적재 위생: 이 보조 수집기(pykrx 레거시 경로)는 공식 경로(KRX OpenAPI / KIS)가
    # 넣은 행을 덮어쓰지 않는다(저장소 계층 postgres_storage.py 와 동일 정책 — 같은 플래그).
    # 기본 = 빈 자리만 채우기(DO NOTHING). 되돌리려면 컨테이너에 YF_MARKET_DATA_OVERWRITE=1.
    yf_overwrite = os.getenv('YF_MARKET_DATA_OVERWRITE', '0').strip().lower() in ('1', 'true', 'yes', 'on')
    conflict = """ON CONFLICT (stock_code, trade_date) DO UPDATE SET
            open_price = EXCLUDED.open_price,
            high_price = EXCLUDED.high_price,
            low_price = EXCLUDED.low_price,
            close_price = EXCLUDED.close_price,
            volume = EXCLUDED.volume""" if yf_overwrite else "ON CONFLICT (stock_code, trade_date) DO NOTHING"
    logger.info('market_data 적재 모드: %s', '덮어쓰기(OVERWRITE=1)' if yf_overwrite else '빈 자리만 채우기(DO NOTHING)')
    # 자기신고(R28, 2026-10-03): 적재 전후 market_data 총행수로 **실제 삽입 델타(persisted)** 를 잰다.
    # claimed = 파서가 만든 행수(len(rows)), source = API 수신(n_recv). 셋을 모두 남겨야 멱등 재실행
    # (claimed>0·inserted=0)을 parse_failure 로 오탐하지 않는다.
    try:
        cur.execute('SELECT COUNT(*) FROM market_data'); md_before = int(cur.fetchone()[0])
    except Exception:
        md_before = None
    execute_values(cur, f"""
        INSERT INTO market_data (stock_code, trade_date, open_price, high_price, low_price, close_price, volume)
        VALUES %s
        {conflict}
    """, rows, page_size=1000)
    pg.commit()
    md_after = None
    if md_before is not None:
        try:
            cur.execute('SELECT COUNT(*) FROM market_data'); md_after = int(cur.fetchone()[0])
        except Exception:
            md_after = None
    persisted = (md_after - md_before) if (md_before is not None and md_after is not None) else None
    cur.close()
    pg.close()
    logger.info(f'Daily collection complete. Processed {len(stocks)} stocks.')
    # 자기신고 한 줄 — 호스트 래퍼(full_pipeline_dd.sh::run_docker_phase)가 파싱해 dq_runner_claim 에
    # 기록한다. 컨테이너엔 dq_claim.py 가 없어 러너 내부 배선이 불가능하므로 이 경로가 유일하다.
    # 예외를 삼켜 수집을 깨지 않는다. source>0 AND claimed==0 이면 parse_failure 로 잡힌다.
    try:
        if n_recv > 0:
            print('[claim] yfinance_market_data market_data source=%s claimed=%s persisted=%s'
                  % (n_recv, len(rows), '-' if persisted is None else persisted), flush=True)
    except Exception as e:
        print('[claim] emit failed: %s: %s' % (type(e).__name__, e), file=sys.stderr)
print('yfinance DONE')
PYEOF
run_docker_phase stock_yfinance_collector /tmp/phase_1_1.py 3600
echo "1" > "$PROGRESS_FILE"
sleep 10

# 1-2. KRX: Trading/Short/Derivatives
#   이 단계의 수집기들은 pykrx(레거시 경로)라 KRX_ID/KRX_PW 로그인이 필수다. 자격증명이
#   없으면 **종목마다** 로그인 실패를 뱉으며 로그를 수천 줄로 채우고 시간을 버린다
#   (실측 2026-09-24: 9,000여 줄 / "KRX 로그인 실패" 반복). 미설정이면 사전에 건너뛴다.
if [ -n "${KRX_ID:-}" ] && [ -n "${KRX_PW:-}" ]; then
    echo "--- 1-2. KRX: Trading/Short/Derivatives ---"
    docker exec -i stock_krx_collector sh -c 'cat > /tmp/phase_1_2.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
from app.main import KrxCollectorService
import logging; logging.basicConfig(level=logging.INFO)
logging.raiseExceptions = False
KrxCollectorService().run_daily_collection()
print('KRX DONE')
PYEOF
    run_docker_phase stock_krx_collector /tmp/phase_1_2.py 600
else
    echo "--- 1-2. KRX: 건너뜀 (KRX_ID/KRX_PW 미설정 — 공매도·수급·파생 수집 불가) ---"
fi
echo "2" > "$PROGRESS_FILE"
sleep 30

# 1-3. News Analyzer: News + Sentiment
echo "--- 1-3. News Analyzer: News + Sentiment ---"
docker exec -i stock_news_analyzer sh -c 'cat > /tmp/phase_1_3.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import asyncio
import logging; logging.basicConfig(level=logging.INFO)
from app.main import NewsAnalyzerService
async def run():
    s = NewsAnalyzerService()
    # 실제 메서드명은 analyze_recent_articles() 다. run_collection() 은 존재하지 않아
    # 이 phase 가 매일 AttributeError 로 죽었고, 그래서 파이프라인의 '뉴스 수집' 단계는
    # 컨테이너 자체 스케줄(30분 주기 run_scheduled)에만 의존했다(2026-09-28 실측).
    await s.analyze_recent_articles()
    print('News DONE')
asyncio.run(run())
PYEOF
run_docker_phase stock_news_analyzer /tmp/phase_1_3.py 600
echo "3" > "$PROGRESS_FILE"
sleep 30

# 1-4. Economic Calendar: FOMC/Earnings/CPI
echo "--- 1-4. Economic Calendar: FOMC/Earnings/CPI ---"
docker exec -i stock_economic_calendar sh -c 'cat > /tmp/phase_1_4.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import logging; logging.basicConfig(level=logging.INFO)
from app.main import EconomicCalendarService
EconomicCalendarService().run_daily_update()
print('Economic Calendar DONE')
PYEOF
run_docker_phase stock_economic_calendar /tmp/phase_1_4.py 600
echo "4" > "$PROGRESS_FILE"
sleep 30

# 1-5. Financials: PER/PBR/ROE
echo "--- 1-5. Financials: PER/PBR/ROE ---"
docker exec -i stock_yfinance_collector sh -c 'cat > /tmp/phase_1_5.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import logging, os, psycopg2; logging.basicConfig(level=logging.INFO)
logging.raiseExceptions = False
from app.collectors.price_collector import PriceCollector
from app.storage.postgres_storage import PostgresStorage
pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password=os.environ.get('POSTGRES_PASSWORD',''))
cur = pg.cursor()
cur.execute("SELECT stock_code FROM stocks WHERE market = 'KOSDAQ' AND stock_code ~ '^[0-9]' LIMIT 20")
codes = [r[0] for r in cur.fetchall()]; cur.close(); pg.close()
p = PriceCollector(); s = PostgresStorage()
count = 0
for code in codes:
    r = p.collect_fundamentals({'code':code,'market':'KOSDAQ','name':code})
    if r.get('market_cap') or r.get('roe'):
        s.update_fundamentals(r)
        count += 1
print(f'Financials collected: {count}/{len(codes)} stocks')
PYEOF
run_docker_phase stock_yfinance_collector /tmp/phase_1_5.py 600
echo "5" > "$PROGRESS_FILE"
sleep 30

# 1-6. Stock Vectorizer: Embeddings
echo "--- 1-6. Stock Vectorizer: Embeddings ---"
docker exec -i stock_vectorizer sh -c 'cat > /tmp/phase_1_6.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import logging; logging.basicConfig(level=logging.INFO)
from app.main import StockVectorizerService
StockVectorizerService().run_vectorization()
print('Vectorizer DONE')
PYEOF
run_docker_phase stock_vectorizer /tmp/phase_1_6.py 600
echo "6" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 2: ML Training — 챔피언 재학습(최신 데이터) + 보호된 승격
# ==============================================================
# 왜 바뀌었나: 예전 Phase 2 는 /app/scripts/train_quick.py 를 돌렸는데
#   ① Trainer 반환값이 7개로 늘어 unpack 크래시(2026-09-23), ② AUC 는 제거된
#   saved_models/training-result-v15.json 을 읽어 항상 N/A, ③ 학습 결과를
#   app/models/champion 에 바로 덮어써 10종목 스모크 모델이 라이브 챔피언을
#   대체할 수 있었다. 이제 후보 디렉터리 학습 → AUC 게이트 승격으로 바꾼다.
echo ""
echo "=== Phase 2: ML Training (champion retrain on latest data) ==="
CAND_DIR="app/models/champion_cand"
CHAMP_DIR="app/models/champion"
# 낡은 후보가 남아 있으면 재학습 실패 시 그대로 승격되어 버린다 → 먼저 비운다
docker exec stock_xgboost_ml sh -c "rm -rf /app/$CAND_DIR" >> "$LOG_FILE" 2>&1
# 2026-09-30 실측(엔지니어): 종전 예산 `timeout 7200` 은 **매일 124 로 죽고 있었다** — 09-24·25·28·29
#   4회 연속 `챌린저 학습 타임아웃(2시간)`·`exit=124` 로 후보가 만들어지지 않아 승격 심사 자체가 열리지
#   않았고(챔피언 0.551318 2026-09-24 이후 동결), 밤마다 2시간을 태우고 버렸다. 실측 속도는
#   200종목×90일 = 12,101 페어에 **1.25 pair/s** (09-30 20:45 시작 → 47.9%(5,800페어)에서 22:45 SIGTERM)
#   → 피처 빌드만 161분 + 학습 수 분 = 약 **170분** = 종전 7200s(120분)의 1.4배.
#   → 12600s(3.5h)로 올린다. 다시 조정할 때는 `12,101 ÷ 실측 pair/s` 로 계산하고, 빌드는
#   `Build progress: N/12101 stock-date pairs ... N.NN pair/s` 줄에서 매일 실측할 수 있다.
# 학습 규모: 최근 데이터 우선 200종목 × 90일(≈1.2만 패널행).
#   실측 피처 빌드 속도 = 종목-일 쌍당 약 0.45초(199피처 + Neo4j + SNS/매크로 as-of).
#   → 200종목×120일(16,080쌍)은 약 2시간이 걸려 **30분 타임아웃(구 1800s)에서는 후보가
#   완성되지 않아 승격 심사가 아예 열리지 않았다**(실측 2026-09-24: exit=124, 30분에 4,000쌍).
#   그래서 기간을 90일로 줄이고 예산을 2시간으로 올린다. 늘릴 때는 둘을 함께 조정할 것.
docker exec stock_xgboost_ml timeout 12600 sh -c \
    "cd /app && python -m app.training.retrain_champion --days 90 --stock-limit 200 --out-dir $CAND_DIR" \
    >> "$LOG_FILE" 2>&1 < /dev/null
RC=$?
if [ "$RC" -ne 0 ]; then
    if [ "$RC" -eq 124 ]; then
        echo "  챌린저 학습 타임아웃(3.5시간) — 피처 빌드가 예산을 초과. --days/--stock-limit 또는 timeout 조정 필요"
    fi
    echo "  챌린저 학습 실패(exit=$RC) — 챔피언 유지, 승격 생략"
else
    # 승격 게이트: 후보 지표(auc_mean 있으면 그것, 없으면 ensemble_auc) ≥ 0.53 이고
    # **챔피언 기준선** 이상일 때만 교체. 기준선은 champion/robust_auc.json(다중 시드
    # 기록)이 있으면 그 값, 없으면 min(auc.txt, --legacy-baseline-cap 0.53) — 단일 시드
    # 운값(현 챔피언 0.6131)이 승격을 잠그지 못하게 한다. 직전 챔피언은 champion_prev_* 로 백업.
    # 2026-09-25 하향(0.55→0.53): 5폴드×5시드 실측 상한이 ≈0.54 라 0.55 는 **어떤 정직한
    # 후보도 통과 못 하는 잠금**이었고 챔피언이 8/14 이후 동결되어 train/serve skew 만 컸다.
    # 근거: docs/asof_measurement_20260925.md
    #
    # 2026-10-02 (MT116 실측): --min-improvement 0.0 은 **노이즈급 승격**을 통과시켰다 —
    #   전일 후보(val ensemble_auc 0.5548)가 챔피언(0.5513)을 +0.0035 로 이겨 승격됐고,
    #   같은 유니버스·같은 피처행렬 실측에서 그 모델은 0.55 초과 신호가 0건(최대 0.4733)이라
    #   swing 스크리너 batch_type 이 signal→raw_fallback 으로 뒤집혀 소비자가 배치를 전량
    #   거부했다(3세션 연속 swing 무진입). 엔지니어 사전등록 문턱이 "폴드 std ±0.03 →
    #   +0.02 이상만 신호"이므로 게이트도 같은 바닥을 쓴다.
    #   재현: scripts/_swing_ensemble_weight_probe.py · data/reports/mt116_live_score_probe.json
    #   주의: 이 비교는 단일 val split 이라 문턱이 세면 승격이 오래 잠길 수 있다
    #   (챔피언 0.5513 기준 후보 ≥0.5713 필요) — 승격 게이트에 '라이브 스코어 분포' 검사를
    #   추가하는 근본 수리가 되면 그때 이 값을 완화한다(리뷰보드 안건).
    # 증거 측정 전 필수 준비: 순기대(part c)는 후보 CSV(체결성 스윕 결과)를 읽는다.
    #   컨테이너는 `data/reports` 를 마운트하지 않는다(실측 2026-10-04) → 서비스 내부 경로로 복사한다.
    #   (호스트에서 직접 쓰면 권한 거부: 서비스 app/reports 는 컨테이너가 root 로 만든다 → docker cp 로.)
    if [ -f data/reports/close_gate_probe/trades.csv ]; then
        docker cp data/reports/close_gate_probe/trades.csv \
            stock_xgboost_ml:/app/app/reports/close_gate_probe_trades.csv >> "$LOG_FILE" 2>&1 \
            || echo "  (trades.csv 스테이징 실패 — 순기대 증거 없이 진행)" >> "$LOG_FILE"
    fi
    # 다중 폴드 OOS + 순기대 증거 생성(2026-10-02 신설 · 2026-10-03 확장 — 증거 축적, 정책은 objective.json):
    #   실측(8모델 4축): 단일 분할 val AUC 는 다중 폴드 OOS AUC 와 순위 상관 스피어만 **−0.81**, 그리고
    #   AUC 는 순기대와 상관하지 않는다(+0.10/−0.24, n=8). 그래서 승격 증거는 **돈(체결 가능 순기대)** 이다.
    #   `--skip` 없이 전부 측정한다: ②다중폴드 AUC ③순기대/분할표본(+①라이브 신호 수 검증에 재사용).
    #   비용 실측: 피처 빌드 757s + 파트당 수 분 (총 ≈15~20분). 실패해도 파이프라인은 계속한다.
    timeout 2100 docker exec -w /app -e PYTHONPATH=/app stock_xgboost_ml \
        python /app/scripts/model_metric_protocol_audit.py \
        --models "[[\"cand\", \"/app/$CAND_DIR\"]]" \
        --out "/app/app/reports/oos_evidence_${CAND_DIR##*/}.json" \
        --robust-oos-out "/app/$CAND_DIR/robust_oos.json" >> "$LOG_FILE" 2>&1 \
        || echo "  (OOS/순기대 증거 생성 실패 — 비차단, 다음 실행에서 재시도)" >> "$LOG_FILE"
    # 챔피언 기준선(같은 프로토콜) 갱신 — 월요일에만(비용 ≈15분). 게이트는 같은 지표로 비교해야 한다.
    if [ "$(date +%u)" = "1" ]; then
        timeout 2100 docker exec -w /app -e PYTHONPATH=/app stock_xgboost_ml \
            python /app/scripts/model_metric_protocol_audit.py \
            --models "[[\"champion\", \"/app/$CHAMP_DIR\"]]" \
            --out "/app/app/reports/oos_evidence_champion.json" \
            --robust-oos-out "/app/$CHAMP_DIR/robust_oos.json" >> "$LOG_FILE" 2>&1 \
            || echo "  (챔피언 기준선 갱신 실패 — 비차단, 지난 값 유지)" >> "$LOG_FILE"
    fi
    # 라이브 스코어 게이트(2026-10-02 신설, MT116 구조 수리): 후보가 실제로 신호를 내는가.
    #   AUC 게이트는 '배포 스코어 분포가 절대문턱 아래로 내려가 경로가 닫히는' 유형을 볼 수 없다
    #   (실측: val AUC +0.0035 승격이 swing batch_type 을 signal→raw_fallback 으로 뒤집어 3세션 무진입).
    #   프로브가 같은 유니버스·같은 피처행렬에서 후보/챔피언을 채점한다(약 90초).
    #   차단(rc=2) → 승격 생략(챔피언 유지). 측정 실패(rc=3) → AUC 게이트에 위임(차단하지 않는다).
    if python3 scripts/gate_promote_live_score.py --candidate "$CAND_DIR" >> "$LOG_FILE" 2>&1; then
        # 증거 게이트 플래그는 objective.json(단일 진실원)에서 만든다 — 정책과 코드가 어긋나지 않게.
        GATE_FLAGS="$(python3 scripts/objective.py promote-flags 2>> "$LOG_FILE" | tr -d '\n')"
        echo "  승격 게이트 플래그: $GATE_FLAGS" >> "$LOG_FILE"
        docker exec stock_xgboost_ml python -m app.training.champion_promote \
            --candidate "$CAND_DIR" --champion "$CHAMP_DIR" \
            --min-auc 0.53 --min-improvement 0.02 \
            --legacy-baseline-cap 0.53 --max-std 0.05 \
            $GATE_FLAGS \
            --summary-out app/reports/ml_result.json >> "$LOG_FILE" 2>&1 < /dev/null
    else
        echo "  라이브 스코어 게이트: 승격 생략(후보가 문턱 초과 신호 0건 — MT116 유형)" >> "$LOG_FILE"
    fi
fi
# MT117(트레이더 환류): 챔피언 교체/롤백을 릴리스 로그에 남긴다 — 실패해도 파이프라인은 계속한다.
# (2026-10-02 롤백이 어디에도 기록되지 않아 트레이더가 디렉터리 mtime 으로 역추적해야 했다.)
python3 scripts/log_champion_swap.py >> "$LOG_FILE" 2>&1 || true
AUC=$(docker exec stock_xgboost_ml sh -c "cat /app/$CHAMP_DIR/auc.txt" 2>/dev/null | tr -d '\n ')
echo "Best AUC: ${AUC:-N/A} (live champion)"
docker cp stock_xgboost_ml:/app/app/reports/ml_result.json ./reports/ml_result.json 2>/dev/null
echo "  -> reports/ml_result.json"
echo "7" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 3: Swing Analysis (All KOSDAQ)
# ==============================================================
echo ""
echo "=== Phase 3: Swing Analysis (All KOSDAQ) ==="
docker exec -i stock_xgboost_ml sh -c 'cat > /tmp/phase_3.py' << 'PYEOF'
import sys, json, os, psycopg2, numpy as np
sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from datetime import datetime

pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password=os.environ.get('POSTGRES_PASSWORD',''))
cur = pg.cursor()
today = datetime.now().strftime('%Y-%m-%d')
cur.execute("SELECT md.stock_code, s.stock_name, s.sector, md.close_price FROM market_data md JOIN stocks s ON md.stock_code = s.stock_code WHERE md.trade_date = %s AND s.market = 'KOSDAQ' AND md.volume > 0 ORDER BY md.stock_code", (today,))
stocks = cur.fetchall()
pipeline = FeaturePipeline(pg_conn=pg)
ensemble = EnsembleModel(model_dir='app/models/champion')
ensemble.load('app/models/champion')
model_features = ensemble.load_feature_names('app/models/champion')

results = []
for code, name, sector, close in stocks:
    try:
        feats = pipeline.build_features(code, today)
        if feats.get('feature_count', 0) < 10: continue
        X = np.array([[float(feats.get(f, 0.0)) for f in model_features]], dtype=np.float32)
        X = np.nan_to_num(X, nan=0.0)
        prob = float(ensemble.predict(X)[0])
        results.append({'code':code,'name':name,'sector':sector,'close':float(close),'prob':round(prob,4),'dir':'UP' if prob>0.5 else 'DOWN','conf':round(abs(prob-0.5)*2,4)})
    except: pass

results.sort(key=lambda x: x['conf'], reverse=True)
import json as j
with open('/app/reports/swing_candidates.json', 'w') as f:
    j.dump({'date':today,'total':len(results),'up':len([r for r in results if r['dir']=='UP']),'down':len([r for r in results if r['dir']=='DOWN']),'high_confidence':[r for r in results if r['conf']>=0.30],'top_up':[r for r in results if r['dir']=='UP'][:20],'top_down':[r for r in results if r['dir']=='DOWN'][:20]}, f, indent=2, ensure_ascii=False)
print(f'Swing analysis saved: {len(results)} stocks, {len([r for r in results if r["dir"]=="UP"])} UP, {len([r for r in results if r["dir"]=="DOWN"])} DOWN')
pg.close()
PYEOF
run_docker_phase stock_xgboost_ml /tmp/phase_3.py 900
echo "8" > "$PROGRESS_FILE"
docker cp stock_xgboost_ml:/app/reports/swing_candidates.json ./reports/swing_candidates.json 2>/dev/null
echo "  -> reports/swing_candidates.json"

# ==============================================================
# PHASE 4: Backtest
# ==============================================================
echo ""
echo "=== Phase 4: Backtest ==="
docker exec -i stock_xgboost_ml sh -c 'cat > /tmp/phase_4.py' << 'PYEOF'
import sys, json, os, psycopg2, numpy as np
sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.training.trainer import Trainer
from sklearn.metrics import roc_auc_score, accuracy_score

pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password=os.environ.get('POSTGRES_PASSWORD',''))
# 유니버스: KOSPI 30 + KOSDAQ 20 층화 무작위 (ETF/ETN 제외, seed 42 고정 → 재현 가능)
# 2026-08 수정: 기존 ORDER BY stock_code LIMIT 50 (코드순 편향 + 채권/콩 ETN 다수) 제거
from app.training.universe import select_backtest_universe
stocks_list = select_backtest_universe(pg, n_kospi=30, n_kosdaq=20, min_days=30, seed=42)
print('Backtest universe:', len(stocks_list), 'stocks')
pipeline = FeaturePipeline(pg_conn=pg); trainer = Trainer(storage=None, feature_pipeline=pipeline)
from datetime import datetime, timedelta
_end = datetime.now(); _start = _end - timedelta(days=90)
df = pipeline.build_training_features(stocks_list, _start.strftime('%Y-%m-%d'), _end.strftime('%Y-%m-%d'))
if df is None or len(df) < 100: print('Backtest FAILED - no data'); exit()
if 'date' in df.columns: df = df.sort_values('date').reset_index(drop=True)
y = trainer._create_labels(df)
ensemble = EnsembleModel(model_dir='app/models/champion')
ensemble.load('app/models/champion')
# 챔피언 json 계약 순서 0-fill 행렬 (2026-08: 트레이너 분산 필터가 상수 피처를 제거해
# 폭 불일치 CatBoostError 발생 → prepare_training_data 경유 대신 직접 구성)
model_f = ensemble.load_feature_names('app/models/champion')
X = np.zeros((len(df), len(model_f)), dtype=np.float32)
for _j, _f in enumerate(model_f):
    if _f in df.columns:
        X[:, _j] = np.nan_to_num(df[_f].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
_valid = ~np.isnan(y); X, y = X[_valid], y[_valid]
probs = ensemble.predict(X)
try: auc = roc_auc_score(y, probs)
except: auc = 0.5
acc = accuracy_score(y, (probs>0.5).astype(int))
print(f'Backtest: AUC={auc:.4f}, ACC={acc:.4f}, Samples={len(y)}, Features={len(model_f)}')
import json as j
with open('/app/reports/backtest_result.json','w') as f:
    j.dump({'auc':round(auc,4),'accuracy':round(acc,4),'samples':len(y),'features':len(model_f)}, f)
pg.close()
PYEOF
run_docker_phase stock_xgboost_ml /tmp/phase_4.py 900
echo "9" > "$PROGRESS_FILE"
docker cp stock_xgboost_ml:/app/reports/backtest_result.json ./reports/backtest_result.json 2>/dev/null
echo "  -> reports/backtest_result.json"

# ==============================================================
# PHASE 5: Strategy Execution
# ==============================================================
echo ""
echo "=== Phase 5: Trading Strategies ==="
docker exec -i stock_strategy_agents sh -c 'cat > /tmp/phase_5.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import logging; logging.basicConfig(level=logging.INFO)
from app.main import StrategyAgentService
StrategyAgentService().run_all_strategies()
print('Strategies DONE')
PYEOF
run_docker_phase stock_strategy_agents /tmp/phase_5.py 300
echo "10" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 5-2: 강환국 팩터 전략 (하면 된다! 퀀트투자)
# Value/Quality/Momentum/LowVol/MultiFactor 5종 — paper-only
# ==============================================================
echo ""
echo "=== Phase 5-2: 강환국 팩터 전략 (Value/Quality/Momentum/LowVol/MultiFactor) ==="
docker exec -i stock_strategy_agents sh -c 'cat > /tmp/phase_5b.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import json, logging, os
from datetime import datetime
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
from app.main import StrategyAgentService

svc = StrategyAgentService()
stock_names = {s["stock_code"]: s.get("stock_name") for s in svc.pg_storage.get_all_stocks()}
results = {}
factor_strategies = [
    ("value_factor", svc.value_strategy),
    ("quality_factor", svc.quality_strategy),
    ("momentum_factor", svc.momentum_strategy),
    ("lowvol_factor", svc.lowvol_strategy),
    ("multifactor", svc.multifactor_strategy),
]
for name, strategy in factor_strategies:
    try:
        signals = strategy.analyze()
        results[name] = {
            "signals": len(signals),
            "top": [{"stock_code": s.get("stock_code"), "name": stock_names.get(s.get("stock_code")),
                     "confidence": round(float(s.get("confidence", 0)), 4)} for s in signals[:10]]
            if isinstance(signals, list) else [],
        }
        print(f"[{name}] signals={len(signals) if isinstance(signals, list) else '?'}")
        if isinstance(signals, list):
            for s in signals[:5]:
                print(f"    {s.get('stock_code')} {stock_names.get(s.get('stock_code'), '')} conf={s.get('confidence')}")
    except Exception as e:
        results[name] = {"error": str(e)}
        print(f"[{name}] FAILED: {e}")

# 리포트 저장
os.makedirs("/app/reports", exist_ok=True)
out = "/app/reports/factor_strategies_result.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump({"generated_at": datetime.now().isoformat(), "strategies": results},
              f, ensure_ascii=False, indent=2)
print(f"factor strategies result -> {out}")
PYEOF
run_docker_phase stock_strategy_agents /tmp/phase_5b.py 300
echo "10.5" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 6: Cleanup old news (2d+)
# ==============================================================
echo ""
echo "=== Phase 6: Data Cleanup ==="
bash scripts/cleanup_news_data.sh
echo "Old news purged."
echo "11" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 7: Summary Report
# ==============================================================
echo ""
echo "=== Phase 7: Pipeline Summary ==="
echo ""
echo "========================================"
echo "  FULL PIPELINE COMPLETE"
echo "  Time: $(date)"
echo "========================================"
echo ""
echo "Results:"
for f in reports/swing_candidates.json reports/backtest_result.json reports/ml_result.json; do
  if [ -f "$f" ]; then
    echo "  $f"
  else
    echo "  (not available) $f"
  fi
done
echo "  - Best AUC: ${AUC:-N/A}"
echo "  - Log: $(ls -t reports/full_pipeline_dd_*.log 2>/dev/null | head -1)"

echo ""
echo "Pipeline finished at: $(date)"
