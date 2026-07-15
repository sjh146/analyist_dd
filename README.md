# analyist_dd -- 한국 주식 AI 기반 자동매매 시스템

DeepSeek 감정 분석 -> pgvector 유사도 검색 -> XGBoost 예측 -> 3개 전략 -> Creon API 매매로 이어지는 전자동 파이프라인. Linux Docker 8개 서비스 + Windows VM 1개로 구성된다.

---

## 시스템 구성도

```ascii
[Linux Server - Docker]
  +-- postgres (pgvector/pg16) ──────────────────────────────────────+
  |   - 시장 데이터, 감정 분석, ML 예측, 포지션, 주문                 |
  |   - pgvector: stock_vectors(1024d), news_analysis(1024d)         |
  |   - HNSW 인덱스 (m=16, ef_construction=200)                      |
  +-- neo4j (5.15 + GDS) ────────────────────────────────────────────+
  |   - Stock, Sector, Theme, Cycle, TwinGroup 노드                  |
  |   - BELONGS_TO, THEME_RELATED, AFFECTS, TWIN_OF 관계             |
  +-- redis (7-alpine, AOF) ─────────────────────────────────────────+
  |   - 전략 신호 pub/sub, 캐시, 크론 트리거                         |
  +-- news-analyzer ─────────────────────────────────────────────────+
  |   - RSS 수집기, 커뮤니티 수집기, DART 공시 수집기                |
  |   - DeepSeek 감정 분석 + 진위 평가                                |
  +-- yfinance-collector ────────────────────────────────────────────+
  |   - 가격/거래량, 재무제표, 거시경제, 수급, 선물/옵션 수집        |
  +-- stock-vectorizer ──────────────────────────────────────────────+
  |   - 가격/감정/재무/통합 임베딩 생성 -> pgvector 저장              |
  +-- xgboost-ml ────────────────────────────────────────────────────+
  |   - 58개 Feature Engineering -> XGBoost 학습/예측                |
  +-- strategy-agents ───────────────────────────────────────────────+
  |   - ThemeStrategy / CycleStrategy / TwinStrategy                 |
  |   - Position Sizer, Stop Loss, Signal Validator                  |
  +-- api-gateway ───────────────────────────────────────────────────+
  |   - FastAPI REST (포트 8000)                                     |
  |   - Redis pub/sub 구독 -> 상태/신호 조회                         |
  +-- stock_network (bridge) ────────────────────────────────────────+
       |
       | Proxmox Bridge Network ─────────────────────────────────────┐
       v                                                             |
[Windows VM]                                                        |
  +-- trade-executor (Python 32-bit)                                 |
  |   - Creon API (대신증권) 매매체결                                 |
  |   - Redis Client로 Linux 측과 통신 ◄──────────────────────────────┘
  |   - Order Manager, Balance Checker, Position Checker             |
  +-- Creon Plus (대신증권 HTS)                                      |
```

---

## 서비스 상세

| 서비스 | 언어 | 실행 주기 | 포트 | 설명 |
|--------|------|-----------|------|------|
| postgres | - | 상시 | 5432 | pgvector/pg16, 시장/감정/ML/주문 데이터 |
| neo4j | - | 상시 | 7474, 7687 | Neo4j 5.15 + Graph Data Science 플러그인 |
| redis | - | 상시 | 6379 | Redis 7 AOF, 메시지 큐 + 캐시 |
| news-analyzer | Python 3.11 | 30분 | - | RSS/커뮤니티/DART 수집 + DeepSeek 감정 분석 |
| yfinance-collector | Python 3.11 | 1시간 | - | yfinance 기반 시장/재무/거시/수급/파생 수집 |
| stock-vectorizer | Python 3.11 | 6시간 | - | 4종 임베딩 생성 -> pgvector 저장 |
| xgboost-ml | Python 3.11 | 1시간 | - | 58개 특징 -> XGBoost 학습/예측 |
| strategy-agents | Python 3.11 | 5분 | - | 3개 전략 신호 생성 + 리스크 관리 |
| api-gateway | Python 3.11 | 상시 | 8000 | FastAPI REST, Redis pub/sub 구독 |
| trade-executor | Python 3.11 (32bit) | 실시간 | - | Creon API 매매체결 (Windows VM) |

---

## 58개 Feature 구성

| 모듈 | Feature 수 | 설명 |
|------|-----------|------|
| market_features | 22 | 가격(8): price, return_1d/5d/20d, volatility_20d/60d, ma_position_5/20/60/120 |
| | | 기술(8): RSI, MACD, 볼린저밴드, ATR, OBV, MFI, 스토캐스틱 |
| | | 수급(4): 외국인/기관/개인 순매수, 수급 강도 |
| | | 파생(2): 선물가격, 옵션 PCR |
| company_features | 12 | 재무(8): 매출/영업이익/순이익, PER/PBR/ROE/부채비율 |
| | | 파생(4): 영업이익률/순이익률, 매출증가율, 이익률변화 |
| | | 백분위(2): PER/PBR 업종 내 백분위 |
| sentiment_features | 10 | 감정(5): 평균감정(1d/5d/20d), 감정추세, 감정변동성 |
| | | 뉴스(2): 뉴스건수(5d/20d) |
| | | 진위(1): 진위평균 |
| | | 비율(2): 긍정/부정 비율 |
| | | 공시(1): 공시건수(5d) |
| macro_features | 12 | 금리(3): 기준금리, 금리변화, 금리모멘텀 |
| | | 환율(2): USD/KRW, 환율변화 |
| | | 원자재(2): WTI 유가, 유가변화 |
| | | 물가(2): CPI, PPI |
| | | 채권(2): 국고채3년, 신용스프레드 |
| | | 신규(1): 회사채3년 |
| graph_features | 8 | 섹터(2): 섹터수, 섹터모멘텀 |
| | | 테마(3): 테마수, 최대관련도, 테마모멘텀 |
| | | 쌍둥이(2): 쌍둥이수, 평균상관계수 |
| | | 사이클(1): 사이클위상 (one-hot) |
| vector_features | 6 | 유사도(4): 평균유사도(top10), 최대유사도, 유사도표준편차, 유사종목수 |
| | | 수익률(2): 유사종목평균수익률, 유사종목수익률표준편차 |
| **합계** | **70** | (58 unique + 12 중복/메타) |

---

## 3가지 트레이딩 전략

### 1. ThemeStrategy -- 테마주 매매
pgvector cosine similarity로 유사 종목 그룹을 찾아 테마 리더를 추종한다. 임계값 0.75 이상, 그룹 3~15개 종목, 5일 리밸런싱. 포지션 15%, 손절 7%, 익절 15%.

### 2. CycleStrategy -- 사이클 순환 매매
시장 국면을 감지하여 섹터 로테이션을 수행한다. 60일 lookback, 20일 cycle detection window. 섹터 익스포저 최대 30%, 개별 포지션 10%.

### 3. TwinStrategy -- 쌍둥이 매매
상관계수 0.80 이상인 종목쌍의 Z-score divergence/convergence를 이용한 pairs trading. Z-score 2.0 진입, 0.5 청산. 포지션 8%.

---

## 필수 환경변수 (10개)

| 변수 | 설명 | 예시 |
|------|------|------|
| DEEPSEEK_API_KEY | DeepSeek API 키 | sk-... |
| POSTGRES_PASSWORD | PostgreSQL 비밀번호 | ... |
| NEO4J_PASSWORD | Neo4j 비밀번호 | ... |
| REDIS_PASSWORD | Redis 비밀번호 | ... |
| BRIDGE_VM_IP | Windows VM IP | 192.168.1.101 |
| DART_API_KEY | DART OpenAPI 키 (재무제표/공시) | ... |
| ECOS_API_KEY | 한국은행 ECOS API 키 (거시경제) | ... |
| CREON_USER | 대신증권 Creon ID | ... |
| CREON_PASS | 대신증권 Creon 비밀번호 | ... |
| CREON_CERT | 대신증권 공인인증서 비밀번호 | ... |

---

## 실행 방법

### 1. Linux Docker 서비스 실행
```bash
cp .env.example .env
# .env 파일에 API 키, 비밀번호 설정 (위 10개 변수 필수)

docker-compose up -d
docker-compose ps

# 로그 확인
docker-compose logs -f --tail=100 news-analyzer
docker-compose logs -f --tail=100 xgboost-ml
docker-compose logs -f --tail=100 strategy-agents
```

### 2. Windows VM 설정
```bash
# Windows VM (Proxmox Bridge, IP: ${BRIDGE_VM_IP})에서 실행
python -m pip install -r services/trade-executor/requirements.txt
python services/trade-executor/main.py
```

### 3. 모니터링
```bash
# API Gateway 상태 확인
curl http://localhost:8000/health
curl http://localhost:8000/strategies/status
curl http://localhost:8000/positions

# Redis 신호 모니터링
redis-cli -a ${REDIS_PASSWORD} SUBSCRIBE trade:signals

# PostgreSQL 직접 조회
docker exec -it stock_postgres psql -U stock_user -d stock_trading \
  -c "SELECT * FROM ml_predictions ORDER BY created_at DESC LIMIT 10;"
```

---

## 프로젝트 구조

```
analyist_dd/
├── docker-compose.yml          # 8개 Docker 서비스 + 볼륨/네트워크
├── .env.example                # 환경변수 템플릿 (10개 필수)
├── .gitignore
├── oh-my-openagent.json
├── config/
│   ├── strategies/
│   │   └── strategies.yaml     # 3개 전략 파라미터
│   └── news_sources/
│       └── news_sources.yaml   # RSS/커뮤니티 수집 대상
├── init-scripts/
│   ├── postgres/
│   │   └── 01_schema.sql       # 12개 PG 테이블 + pgvector + HNSW
│   └── neo4j/
│       └── 01_schema.cypher    # 6개 노드 + 4개 관계 + 제약조건
├── services/
│   ├── news-analyzer/          # DeepSeek 감정 분석
│   │   ├── app/
│   │   │   ├── collectors/     # rss, community, dart
│   │   │   ├── analyzers/      # deepseek_analyzer
│   │   │   ├── storage/        # postgres, neo4j
│   │   │   └── models/         # schemas
│   │   └── Dockerfile
│   ├── yfinance-collector/     # 시장 데이터 수집
│   │   ├── app/
│   │   │   ├── collectors/     # price, financial, macro, supply, derivatives, stock_list
│   │   │   ├── processors/     # data_cleaner, technical_indicators
│   │   │   └── storage/        # postgres
│   │   └── Dockerfile
│   ├── stock-vectorizer/       # 종목 벡터화
│   │   ├── app/
│   │   │   ├── vectorizers/    # price, sentiment, fundamental, combined
│   │   │   ├── feature_engine/ # market, price, volume
│   │   │   └── storage/        # postgres, pgvector
│   │   ├── models/             # embedding_models
│   │   └── Dockerfile
│   ├── xgboost-ml/             # ML 예측
│   │   ├── app/
│   │   │   ├── feature_engine/ # market(22), company(12), sentiment(10), macro(12), graph(8), vector(6)
│   │   │   ├── training/       # trainer
│   │   │   ├── inference/      # predictor
│   │   │   ├── models/         # model_manager, xgboost_model
│   │   │   └── storage/        # postgres
│   │   ├── tests/
│   │   └── Dockerfile
│   ├── strategy-agents/        # 매매 전략
│   │   ├── app/
│   │   │   ├── strategies/     # theme, cycle, twin (base)
│   │   │   ├── signals/        # signal_generator, signal_validator
│   │   │   ├── risk_management/ # position_sizer, stop_loss
│   │   │   └── storage/        # postgres, redis
│   │   └── Dockerfile
│   ├── trade-executor/         # Creon API 매매 (Windows VM)
│   │   ├── executors/          # creon_executor, order_manager
│   │   ├── risk_management/    # balance_checker, position_checker
│   │   ├── monitoring/         # order_monitor
│   │   └── utils/              # redis_client
│   └── api-gateway/            # REST API
│       ├── app/
│       └── Dockerfile
├── data/
│   ├── market/                 # 시장 데이터 캐시
│   ├── financials/             # 재무제표 캐시
│   ├── macro/                  # 거시경제 캐시
│   ├── disclosures/            # 공시 데이터 캐시
│   ├── training/               # 학습 데이터
│   └── vector_cache/           # 벡터 캐시
└── models/
    └── saved_models/           # XGBoost 모델 저장
```

---

## 데이터베이스 스키마

### PostgreSQL (12개 테이블 + pgvector)

| 테이블 | 용도 | 주요 컬럼 |
|--------|------|----------|
| stocks | 종목 마스터 | stock_code, stock_name, market, sector, industry, market_cap |
| market_data | OHLCV 시장 데이터 | stock_code, trade_date, open/high/low/close, volume, trading_value |
| stock_vectors | pgvector 임베딩 (1024d) | stock_code, vector_type, embedding(vector), metadata(JSONB) |
| | | HNSW 인덱스 (vector_cosine_ops, m=16, ef_construction=200) |
| news_analysis | 뉴스/SNS 분석 결과 | source, title, sentiment_score, authenticity_score, embedding(vector) |
| | | HNSW 인덱스 (news_embedding) |
| stock_sentiment | 종목별 감정 집계 | avg_sentiment, positive/negative/neutral_count, news/sns_count |
| ml_predictions | ML 예측 결과 | predicted_direction, predicted_change_pct, confidence, features_used |
| trade_orders | 매매 주문 | order_type, quantity, price, order_status, strategy_name |
| positions | 포지션 | quantity, avg_buy_price, unrealized/realized_pnl |
| strategy_config | 전략 설정 | strategy_name, strategy_type, parameters(JSONB), is_active |
| risk_management | 리스크 규칙 | rule_name, rule_type, parameters(JSONB), is_active |
| financial_statements | 재무제표 | revenue, operating_profit, net_income, PER, PBR, ROE, debt_ratio |
| macro_indicators | 거시경제 지표 | indicator_name, date, value, unit |
| foreign_institutional | 외국인/기관 수급 | foreign/institution/individual_net_buy |
| futures_options | 선물/옵션 | futures_price, options_volume, basis, put_call_ratio |

### Neo4j (6개 노드 + 4개 관계)

**노드:** Stock, Sector(8), Industry, Theme(8), Cycle(4), TwinGroup, Disclosure, MacroIndicator

**관계:**
- (Stock)-[:BELONGS_TO]->(Sector) -- 종목-섹터
- (Sector)-[:THEME_RELATED {relevance}]->(Theme) -- 섹터-테마
- (Cycle)-[:AFFECTS {strength}]->(Sector) -- 사이클-섹터
- (Stock)-[:TWIN_OF {correlation}]->(Stock) -- 쌍둥이 종목
- (Stock)-[:FOLLOWS_CYCLE {phase}]->(Cycle) -- 종목-사이클
- (Stock)-[:FILED]->(Disclosure) -- 종목-공시
- (Disclosure)-[:RELATES_TO]->(Sector) -- 공시-섹터

---

### 4. API 레퍼런스

`services/api-gateway`가 8000번 포트로 제공하는 13개 GET 전용 엔드포인트.

⚠️ **API Gateway에 인증/인가가 구현되어 있지 않다.** 포트 노출 시 누구나 주문/포지션을 조회할 수 있다. 모든 엔드포인트는 **GET 전용**이며, POST/PUT/DELETE는 미구현 상태다.

#### 헬스 체크 & 시스템 상태

```bash
# 헬스 체크
curl http://localhost:8000/health
# 응답: {"status": "ok"}

# 시스템 상태 (PostgreSQL 연결 확인)
curl http://localhost:8000/api/v1/status
# 응답: {"db_connected": true, "services": {...}, "timestamp": "..."}
```

#### 종목 정보

```bash
# 종목 목록 (KOSPI, 페이지네이션)
curl "http://localhost:8000/api/v1/stocks?market=KOSPI&sector=&skip=0&limit=100"
# 응답: {"stocks": [...], "total": 800, "skip": 0, "limit": 100}

# 특정 종목 상세
curl http://localhost:8000/api/v1/stocks/005930
# 응답: {"stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI",
#        "sector": "전기전자", "industry": "반도체", "market_cap": 400000}
```

#### 시장 데이터 & 감정 분석

```bash
# 시장 데이터 (최근 30일)
curl "http://localhost:8000/api/v1/stocks/005930/market-data?days=30"
# 응답: {"stock_code": "005930", "data": [{"trade_date": "...", "close": 70000, ...}]}

# 감정 데이터 (최근 30일)
curl "http://localhost:8000/api/v1/stocks/005930/sentiment?days=30"
# 응답: {"stock_code": "005930", "avg_sentiment": 0.65, "trend": "up", ...}
```

#### pgvector 유사 종목 검색

```bash
# 유사 종목 검색 (cosine similarity)
curl "http://localhost:8000/api/v1/vectors/similar/005930?top_k=10&vector_type=combined"
# 응답: {"stock_code": "005930", "similar_stocks": [
#   {"stock_code": "000660", "similarity": 0.89, "stock_name": "SK하이닉스"}, ...]}
# vector_type: price, sentiment, fundamental, combined (기본값)
```

#### ML 예측

```bash
# 특정 종목 예측
curl "http://localhost:8000/api/v1/predictions/005930?days=7"
# 응답: {"stock_code": "005930", "predictions": [
#   {"date": "...", "direction": "up", "change_pct": 1.2, "confidence": 0.78}, ...]}

# 상위 예측 종목 (방향별)
curl "http://localhost:8000/api/v1/predictions/top?top_n=10&direction=up"
# direction: up (상승 예측), down (하락 예측)
# 응답: {"predictions": [{"stock_code": "...", "direction": "up", "confidence": 0.85}, ...]}
```

#### 트레이딩 정보

```bash
# 주문 내역 (상태별 필터)
curl "http://localhost:8000/api/v1/trading/orders?status=pending&limit=50"
# status: pending, filled, cancelled, all
# 응답: {"orders": [{"order_id": 1, "stock_code": "005930", ...}]}

# 현재 포지션
curl http://localhost:8000/api/v1/trading/positions
# 응답: {"positions": [{"stock_code": "005930", "quantity": 10, "avg_buy_price": 68000, ...}]}
```

#### 전략 & 대시보드

```bash
# 전략 설정 조회
curl http://localhost:8000/api/v1/strategies
# 응답: {"strategies": [
#   {"name": "ThemeStrategy", "is_active": true, "parameters": {...}}, ...]}

# 대시보드 요약
curl http://localhost:8000/api/v1/dashboard/summary
# 응답: {"total_predictions": 1500, "active_positions": 3, "total_pnl": 150000, ...}
```

---

### 5. 데이터 파이프라인 흐름

Linux Docker 서비스와 Windows VM 간 데이터 흐름은 6단계로 구성된다.

```
[1. 수집]                         [2. 벡터화]
  yfinance-collector ─────────┐   stock-vectorizer
  (OHLCV/재무/거시/수급/파생)  ├→  (가격/감정/재무/통합 임베딩)
  news-analyzer ──────────────┘   ↓
  (RSS 5개사 + DART 공시)      →  pgvector (HNSW 인덱스)
  + DeepSeek 감정 분석
  ↓ PostgreSQL + Neo4j

[3. 특징 엔지니어링]            [4. ML 예측]
  xgboost-ml                    XGBoost/CatBoost/LightGBM 앙상블
  70개 특징 생성                → 방향 예측 (up/down)
  (market/company/sentiment/    + 신뢰도
   macro/graph/vector)          → PostgreSQL 저장

[5. 전략 신호 생성]             [6. 매매 체결 ─ Windows VM]
  strategy-agents               trade-executor
  ThemeStrategy                 ↓
  CycleStrategy                 Redis Streams 구독
  TwinStrategy                  → Creon API (대신증권 COM)
  → SignalValidator             → OrderManager
  → PositionSizer               → BalanceChecker
  → Redis Streams 발행           → PositionChecker
```

**Linux↔Windows 통신:** `services/shared/redis_streams.py`의 Redis Streams(`xadd`/`xreadgroup`)를 사용한다. Linux 서비스가 Redis Streams에 신호를 발행하면, Windows VM의 trade-executor가 Consumer Group으로 구독하여 처리한다. pub/sub 대신 Streams를 사용했으므로 구독 중단 시에도 메시지가 손실되지 않는다.

---

### 6. 백테스팅 & 리스크 분석

`services/backtester/` 모듈은 3가지 도구를 제공한다.

#### BacktestRunner (`runner.py`)

백테스팅 실행기로 과거 데이터 기반 전략 성능을 검증한다.

```python
from services.backtester.runner import BacktestRunner

r = BacktestRunner()
result = r.run_backtest('theme_trading', ['005930'], '2024-01-01', '2024-06-30')
# 반환: BacktestResult(backtest_id=..., sharpe_ratio=1.8, max_drawdown=-0.12,
#                       win_rate=0.65, total_return=0.18, trades=[...])
```

- `BacktestTrade`: 날짜, 종목코드, 신호(entry/exit), 손익(PnL)
- `BacktestResult`: Sharpe Ratio, Maximum Drawdown, Win Rate, Total Return

#### MonteCarloEngine (`monte_carlo.py`)

기하 브라운 운동(GBM) 기반 리스크 분석 엔진. 10,000회 시뮬레이션으로 포트폴리오 리스크를 추정한다.

| 지표 | 설명 |
|------|------|
| VaR 95% | 95% 신뢰수준 최대 손실 |
| VaR 99% | 99% 신뢰수준 최대 손실 |
| CVaR | Conditional VaR (꼬리 손실 평균) |
| Sharpe Ratio | 위험 대비 초과 수익 |
| Sortino Ratio | 하방 위험 대비 수익 |
| Max Drawdown | 최대 낙폭 |

`batch` 모드로 다수 종목 동시 분석 가능.

#### PaperTradingGate (`paper_trading.py`)

모드 전환 게이트. 항상 `paper` 모드로 시작하며, Sharpe > 1.0 조건을 30일 연속 충족 시 `real` 전환을 제안한다. 실전 전환은 항상 사람 승인이 필요하다.

```python
gate = PaperTradingGate()
gate.switch_to_real(approved=True)  # 사람 승인 후 전환
```

---

### 7. 모니터링 & 알림

`docker-compose.yml`에 정의된 3개 모니터링 도구.

#### Prometheus (포트 9090)

`services/shared/metrics.py`에 정의된 메트릭을 수집한다.

| 메트릭 | 타입 | 레이블 | 설명 |
|--------|------|--------|------|
| `data_collected_total` | Counter | service, source | 서비스별/소스별 데이터 수집량 |
| `features_computed_total` | Counter | service | 특징 엔지니어링 건수 |
| `prediction_latency_seconds` | Histogram | model | ML 예측 지연 시간 |
| `sentiment_analysis_total` | Counter | source | 감정 분석 건수 |
| `signal_generated_total` | Counter | strategy | 전략 신호 생성 건수 |
| `trade_executed_total` | Counter | type | 매매 체결 건수 |
| `db_query_latency_seconds` | Histogram | db, operation | DB 쿼리 지연 시간 |

#### Grafana (포트 3000)

- 기본 계정: `admin` / `admin` (최초 로그인 시 변경 가능)
- 대시보드 프로비저닝: `config/grafana/` 디렉토리에 설정 파일 위치
- Prometheus를 데이터 소스로 사용

#### Jenkins (포트 8080)

- `config/jenkins/init.groovy`로 사전 설정
- `config/jenkins/plugins.txt`로 플러그인 자동 설치
- docker-compose logs로 접속하여 로그 확인

---

### 8. 도커 관리

#### 생명주기

```bash
docker-compose up -d                    # 전체 서비스 시작
docker-compose down                     # 전체 서비스 중지
docker-compose restart [서비스명]       # 특정 서비스 재시작
```

#### 로그

```bash
docker-compose logs -f --tail=100 [서비스명]  # 특정 서비스 로그 실시간 확인
docker-compose logs -f                        # 전체 서비스 로그
```

#### 개별 재빌드

```bash
docker-compose build [서비스명] && docker-compose up -d [서비스명]
```

#### 상태 확인

```bash
docker-compose ps       # 컨테이너 상태
docker stats            # 실시간 리소스 사용량
docker-compose top      # 실행 중 프로세스
```

#### DB 직접 접속

```bash
# PostgreSQL
docker exec -it stock_postgres psql -U stock_user -d stock_trading

# Redis
docker exec -it stock_redis redis-cli -a ${REDIS_PASSWORD}

# Neo4j Browser: http://localhost:7474
```

#### 볼륨 & 네트워크

```bash
docker volume ls                    # 볼륨 목록
docker volume inspect [볼륨명]       # 볼륨 상세
docker network inspect stock_network # 네트워크 상세
```

#### 헬스체크

```bash
docker inspect --format='{{.State.Health.Status}}' [컨테이너명]
```

---

### 9. 트러블슈팅

#### 1. DB 연결 실패

각 데이터베이스 연결 상태를 개별 확인한다.

```bash
docker exec stock_postgres pg_isready -U stock_user
wget -qO- http://localhost:7474 && echo "Neo4j OK"
docker exec stock_redis redis-cli ping  # PONG 응답 확인
```

#### 2. 컨테이너 재시작 루프

```bash
docker-compose logs [서비스명]                      # 오류 원인 확인
docker inspect --format='{{.State.Health.Status}}' [컨테이너명]  # 헬스체크 상태
```

#### 3. 전략 실행 오류

`services/strategy-agents/app/strategies/`의 `base_strategy.py`에서 추상 메서드 `analyze()`가 구현되지 않으면 RuntimeError가 발생한다. 각 전략(ThemeStrategy, CycleStrategy, TwinStrategy)이 `analyze()`를 구현했는지 확인한다.

#### 4. DART API 수집 안 됨

- `.env`의 `DART_API_KEY` 유효성 확인
- `dart_collector.py`의 `corp_code` 파라미터가 DART OpenAPI 명세와 일치하는지 검증
- DART OpenAPI는 `crtfc_key` 파라미터명을 사용함 (`api_key` 아님)

#### 5. Redis 신호 손실

기존 pub/sub 방식은 구독자가 없으면 메시지를 폐기한다. `services/shared/redis_streams.py`의 Redis Streams(`xadd`/`xreadgroup`)가 올바르게 사용되고 있는지 확인한다. Consumer Group 미등록 시 메시지가 손실될 수 있다.

#### 6. schedule 라이브러리 행 (Hang)

`schedule.run_pending()`이 60초마다 정상 호출되는지 확인한다. 태스크가 Hang 상태가 되면 이후 모든 태스크가 스킵된다. `apscheduler` 또는 `celery`로 교체를 고려한다.

#### 7. Windows VM 연결 안 됨

`services/trade-executor/config.py`의 `BRIDGE_VM_IP`, `BRIDGE_HOST` 설정이 실제 네트워크와 일치하는지 확인한다. Proxmox Bridge 네트워크에서 양방향 통신이 가능한지 ping으로 확인한다.

#### 8. .env 파일 누락

```bash
cp .env.example .env
# 모든 필수 변수(10개)가 설정되었는지 확인
```

---

### 10. 개발 환경 설정

#### 1. 클론 및 설정

```bash
git clone <repository-url>
cd analyist_dd
cp .env.example .env
# .env 파일에 DEEPSEEK_API_KEY, POSTGRES_PASSWORD 등 10개 필수 변수 설정
```

#### 2. Python 가상환경

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r services/*/requirements.txt
```

#### 3. 개별 서비스 실행

```bash
# Docker 없이 로컬에서 개별 서비스 실행 가능
python -m services.news-analyzer.app.main
python -m services.yfinance-collector.app.main
python -m services.stock-vectorizer.app.main
python -m services.xgboost-ml.app.main
python -m services.strategy-agents.app.main
python -m services.api-gateway.app.main
```

#### 4. 테스트 실행

```bash
pytest services/xgboost-ml/tests/
pytest tests/
```

#### 5. Docker 없이 로컬 개발

PostgreSQL, Neo4j, Redis를 Docker로 실행하고 서비스는 로컬에서 `python -m`으로 실행한다.

```bash
docker-compose up -d postgres neo4j redis
python -m services.news-analyzer.app.main
```

로컬 실행 시 서비스 간 통신은 `localhost`를 사용한다.

#### 6. Windows VM 개발

```bash
# Windows VM (Proxmox)에서 실행
pip install -r services/trade-executor/requirements.txt
python services/trade-executor/main.py
```

Creon Plus (대신증권 HTS)가 설치되어 있어야 하며, 32-bit Python 환경이 필요하다.

#### 7. 스크립트 유틸리티

`scripts/` 디렉토리에 유용한 유틸리티 스크립트가 위치한다.

| 스크립트 | 설명 |
|----------|------|
| `scripts/run_all_tests.sh` | 전체 테스트 실행 |
| `scripts/emergency_revert.sh` | 긴급 복구 (특정 시점으로 롤백) |
| `scripts/run_tests_in_docker.sh` | 도커 컨테이너 내 테스트 실행 |
| `scripts/notify.sh` | 시스템 알림 전송 |

#### 8. 데이터 수집 서비스만 단독 실행

```bash
# DB만 Docker로 실행하고 수집 서비스만 로컬에서 실행
docker-compose up -d postgres neo4j redis
python -m services.yfinance-collector.app.main
```

---

## 알려진 이슈 (분석 기반)

> ⚠️ Momus(Plan Critic) + Oracle(Architecture Consultant) 심층 분석 결과. 실거래 전 반드시 해결해야 한다.

### Critical (실거래 전 필수)

1. **Look-ahead bias** -- ML 모델이 미래 정보를 학습에 사용할 가능성이 있다. train/test split 검증과 시계열 walk-forward validation이 필요하다. 현재 상태로는 예측 성능이 무의미할 수 있다.

2. **3개 전략 전부 Runtime Error** -- ThemeStrategy, CycleStrategy, TwinStrategy 모두 미구현 메서드로 인해 실행 즉시 실패한다. `base_strategy.py`의 추상 메서드가 구현되지 않았다.

3. **DART API 잘못된 파라미터** -- `dart_collector.py`에서 DART OpenAPI 호출 파라미터가 실제 API 명세와 일치하지 않는다. 공시 데이터 수집이 전혀 이루어지지 않는다.

4. **API Gateway 인증/인가 없음** -- `api-gateway`가 8000번 포트로 모든 요청을 인증 없이 처리한다. 포트 노출 시 누구나 주문/포지션을 조회할 수 있다.

5. **Redis pub/sub 신호 손실** -- Redis pub/sub은 구독자가 없으면 메시지를 폐기한다. trade-executor 재시작 또는 네트워크 단절 시 매매 신호가 영구 손실된다. Redis Stream 또는 List 기반으로 변경해야 한다.

### High (조기 수정 권장)

6. **삼성전자를 시장 지수로 사용** -- KOSPI 지수 대신 삼성전자(005930)를 시장 대표값으로 사용한다. 삼성전자 비중이 30%가 넘는 한국 시장에서 왜곡이 심각하다.

7. **Position Sizer가 주가/잔고 무시** -- `position_sizer.py`가 현재 주가와 계좌 잔고를 고려하지 않고 고정 비율로 포지션을 계산한다. 실제 주문 가능 수량과 괴리가 발생한다.

8. **Trade Executor가 PG에 포지션 기록 안 함** -- `creon_executor.py`가 체결 후 PostgreSQL `positions` 테이블을 갱신하지 않는다. 전략 레이어가 현재 포지션을 알 수 없다.

9. **5개 중복 PostgreSQL 연결 풀** -- 각 서비스가 독립적인 DB 연결 풀을 생성한다. 총 5개 서비스 x 기본 풀 = 과도한 연결. 중앙化管理 또는 최대 연결 수 제한이 필요하다.

10. **모니터링/알림/관측 불가** -- 장애 감지, 성능 메트릭, 알림 시스템이 전혀 없다. 컨테이너가 죽어도 감지할 방법이 없다.

11. **백테스팅/리스크 모델링 없음** -- 전략 검증을 위한 백테스팅 프레임워크가 없다. 리스크 모델링(VaR, Monte Carlo)도 구현되지 않았다.

12. **Windows VM 자동 재시작 없음** -- trade-executor가 실행되는 Windows VM의 자동 재시작/복구 전략이 없다. VM 재부팅 시 수동 실행이 필요하다.

### Medium (예정된 장애)

13. **schedule 라이브러리 Hang 시 태스크 스킵** -- `schedule` 라이브러리는 태스크가 Hang 상태가 되면 이후 모든 태스크를 스킵한다. `apscheduler` 또는 `celery`로 교체해야 한다.

14. **한국 휴장일 캘린더 없음** -- 한국 증시 휴장일(공휴일, 임시휴장)을 고려하지 않는다. 휴장일에도 데이터 수집/매매 시도가 발생한다.

15. **DeepSeek async retry 비호환** -- `deepseek_analyzer.py`의 retry 로직이 asyncio와 호환되지 않는다. 타임아웃/재시도가 정상 동작하지 않는다.

16. **KOSPI200 선물 정적 티커 오류** -- 선물 티커가 하드코딩되어 있어 월물 변경 시 자동 대응이 불가능하다.

17. **중복 제거 Set 100개마다 초기화** -- 뉴스 중복 제거 Set이 100개 처리마다 초기화되어 동일 뉴스가 반복 처리된다.

18. **.env 파일 저장소 포함** -- `.env` 파일이 `.gitignore`에 등록되어 있지만 이미 저장소에 포함되어 있다. 실제 API 키가 노출될 위험이 있다.

19. **Docker 컨테이너 root 실행** -- 모든 컨테이너가 root 사용자로 실행된다. 보안 취약점 발생 시 호스트 시스템이 위험에 노출된다.

20. **DB 백업 전략 없음** -- PostgreSQL, Neo4j, Redis AOF의 백업/복구 전략이 정의되지 않았다. 데이터 유실 시 복구가 불가능하다.

---

## 권장 사항

**3개월 Paper Trading 후 실전 투입 권장.**

1. Critical 5개 이슈 선해결 (look-ahead bias, 전략 runtime error, DART API, API Gateway 인증, Redis 신호 손실)
2. Paper Trading 환경에서 3개월 이상 전략 검증
3. High 이슈 순차 개선 (시장 지수, position sizer, 모니터링, 백테스팅)
4. Medium 이슈는 운영 중 지속 개선
5. 실전 투입 전 보안 감사 필수 (API 키 노출, root 실행, 인증/인가)
