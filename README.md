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
