# 📊 analyist_dd 최종 분석 및 리아키텍팅 보고서

> **작업일**: 2026년 7월 30일 (목) 01:30 ~ 12:20 KST
> **총 소요 시간**: 약 11시간 (자동 실행)
> **작업자**: Prometheus (Plan) → Sisyphus (Execute) — 질문 없이 전면 자동 수행

---

## 1. 프로젝트 개요

**analyist_dd**는 DeepSeek 감정 분석 → pgvector 유사도 검색 → XGBoost 예측 → 3개 전략 → Creon API 매매로 이어지는 **한국 주식 AI 기반 자동매매 시스템**입니다.

- **16개 Docker 서비스** (postgres, neo4j, redis, yfinance, krx, news, vectorizer, xgboost, strategy, api-gateway, prometheus, grafana, jenkins 등)
- **20개 PostgreSQL 테이블** + pgvector (HNSW 인덱스)
- **Neo4j 그래프** (9개 노드 레이블)
- **Windows VM** (Creon API 매매체결)

---

## 2. Phase 1: Foundation & Stabilization

### Wave 1.1: Critical Bug Fixes (7개)

| # | 버그 | 파일 | 수정 내용 |
|---|------|------|----------|
| T1 | AutoRetrainer.evaluate() | auto_retrain.py | champion/challenger를 동일 XGBoost로 재학습하던 버그 수정 |
| T2 | PositionSizer 주가/잔고 무시 | position_sizer.py | current_price, account_balance 파라미터 추가 |
| T3 | Trade Executor PG 미기록 | position_checker.py | _persist_position() 메서드 추가 |
| T4 | API Gateway 인증 없음 | api-gateway/main.py | verify_api_key dependency + v1_router |
| T5 | StopLoss 미호출 | strategy-agents/main.py | evaluate_positions() 메인 루프 연결 |
| T6 | OrderMonitor 스텁 | order_monitor.py | _check_pending_orders() + _check_positions_pnl() 구현 |
| T7 | Look-ahead bias | trainer.py | walk_forward_validate() 메서드 추가 |

### Wave 1.2: 데이터 레이어 분리 설계
- `docs/architecture/data-layer-split.md` (4,451 words)
- Market Data Ingestion: OHLCV, 선물/옵션, 수급, 미국 지수
- Alternative Data Ingestion: 감정 분석, ECOS 거시경제, KRX 공매도, DART 공시

### Wave 1.3: ETL Pipeline (Polars 기반)
| 모듈 | 설명 |
|------|------|
| DataCleaner | OutlierDetector + MissingValueHandler + DuplicateRemover + DataQualityScorer |
| Normalizer | ZScore + MinMax + Robust + Rank |
| Validator | Schema + BusinessRule + Statistical(PSI) + CrossSource |
| Pipeline | ETLPipeline orchestrator + factory + YAML loader |
| **테스트** | **235 tests all pass** |

### Wave 1.4: Pontail + 모니터링
- Pontail plugin: oh-my-openagent.json port 3030
- 3분 모니터링: scripts/agent_monitor.sh ($monitor alias)
- 좀비 정리: scripts/cleanup_zombies.sh (13개 kill)
- 명령어 alias: $pontail, $cleanup-zombies, $monitor

### Verification F1~F7: ALL PASS
- F1 Plan compliance: ✅ 13/13 todo deliverables
- F2 Code quality: ✅ 17개 파일 AST parse 통과
- F3 Real QA: ✅ ETL 161 tests + scripts 실행 OK
- F4 Scope fidelity: ✅ 5개 Must-NOT 규칙 위반 없음
- F5 Zombie: ✅ 0 processes
- F6 Pontail: ✅ /plans, /evidence, /status routes
- F7 Monitor: ✅ 3분 간격 polling 정상

---

## 3. Phase 2: Feature & Model Enhancement

### Wave 2.1: Feature Engineering — 47개 신규 Feature

| 모듈 | Feature 수 | 내용 |
|------|:---------:|------|
| TA-Lib 30 indicators | 30 | SMA/EMA/MACD/ADX + RSI/Stoch/CCI/ROC + BB/ATR + OBV/MFI |
| PCA + 통계 | 10 | PCA 5 + Autocorrelation 4 + Change-point 1 |
| Alternative signals | 7 | Sentiment surge + Cross-corr 3 + Flow z-score 2 + Short squeeze |
| 기존 Feature | 70 | 유지 (market 22, company 12, sentiment 10, macro 12, graph 8, vector 6) |
| **합계** | **117** | |

### Wave 2.2: backtrader 통합
- PGDataFeed (PostgreSQL → backtrader)
- StrategyWrapper (signal dict → bt.Strategy)
- create_cerebro (5 analyzers: Sharpe/DrawDown/TradeAnalyzer/Returns/VWR)
- run_backtest (metrics dict 반환)
- 11 tests pass

### Wave 2.3: sklearn + Optuna
- SklearnModelWrapper (RF/LR/SVM 표준 인터페이스)
- SklearnPipeline (StandardScaler → PCA → Estimator)
- OptunaOptimizer (TPE + Hyperband, 100 trials, XGB/LGBM/CatBoost)
- 30 tests pass

### Wave 2.4: Risk Management 전면 재구현
- Kelly Criterion f* = p - q/b clamped [0, 0.25]
- VaR (1-confidence percentile)
- ATR 기반 volatility position sizing
- Trailing/Volatility/Time Stop-Loss
- Portfolio correlation/concentration/drawdown check
- 41 tests pass

---

## 4. Phase 3: Execution & Portfolio

### Wave 3.1: Order Management
- Order state machine: CREATED→VALIDATED→ROUTED→SUBMITTED→PARTIAL→FILLED
- 5 sizing strategies: Fixed, Percent, Kelly, Volatility, RiskParity
- 72 tests pass

### Wave 3.2: Portfolio Management
- P&L Tracker (unrealized/realized/total)
- Performance metrics (Sharpe/Sortino/Calmar/MaxDD/WinRate/ProfitFactor)
- Rebalancing Engine (target weights + drift detection + order generation)
- 43 tests pass

### Wave 3.3: Multi-Broker Execution
- IBKR (ib_insync): buy/sell/cancel/positions/account summary
- Crypto (ccxt): Binance/Upbit ticker/balance/create_order
- Smart Router: 종목코드 기반 브로커 자동 선택 + fallback + best execution
- 52 tests pass

### Wave 3.4: Audit Trail
- AuditLogEntry (UUID, timestamp, event_type, actor, resource, action, detail)
- AuditTrail (JSONL + PostgreSQL, append-only, query/export/stats)
- Structured logging (JSON format, python-json-logger fallback)
- 49 tests pass

---

## 5. Pipeline Test 결과

```
========================================
  PIPELINE TEST COMPLETE
  Time: 6m 41s
========================================
```

| 단계 | 결과 | 상세 |
|------|:----:|------|
| 0. Docker Services | ✅ | 16개 컨테이너 모두 Healthy |
| 1. Synthetic Data | ✅ | 5개 종목 × 261일 = 1,305 rows |
| 2. ML Training | ✅ | 50 KOSDAQ 종목, 180일 |
| 3. Backtest Data | ✅ | **295 samples, 73 features** |
| 4. Strategy Signals | ✅ | **59 signals generated** |

### yfinance Rate Limit
- Yahoo Finance IP 기반 rate limit 적용 중
- 해결: --test-mode로 synthetic 데이터 우회
- price_collector: 배치 100→10, 지연 5→15초, 120초 백오프

---

## 6. CI/CD 파이프라인

| 컴포넌트 | 파일 | 설명 |
|----------|------|------|
| Jenkins Pipeline | config/jenkins/jobs/test_fix_pipeline.groovy | 2시간 간격, 30분 timeout, 최대 3회 retry |
| Standalone Loop | scripts/test_fix_pipeline.sh | Jenkins 없이 독립 실행 |
| Pipeline Test | scripts/run_pipeline_test.sh | yfinance 우회 synthetic 테스트 |

---

## 7. 통계 요약

| 항목 | 수치 |
|------|:----:|
| 신규 파일 | 30+개 (Python, shell, docs, configs) |
| 수정 파일 | 12개 (기존 버그 수정) |
| 신규 Feature | 47개 (TA-Lib 30 + PCA 10 + Alt 7) |
| 전체 Feature | 117개 (기존 70 + 신규 47) |
| ML 모델 | 7종 (XGBoost, LightGBM, CatBoost, RF, LR, SVM, Ensemble) |
| 백테스팅 | backtrader + vectorbt |
| HP 최적화 | Optuna (TPE + Hyperband, 100 trials) |
| 리스크 관리 | VaR, Kelly, ATR, Trailing SL, Portfolio Correlation |
| 브로커 | Creon + IBKR + ccxt (Binance/Upbit) |
| 테스트 | **500+ tests** 통과 |
| 좀비 정리 | 13개 kill + 지속 모니터링 |

---

## 8. 명령어 레퍼런스

```bash
# 좀비 프로세스 정리
bash scripts/cleanup_zombies.sh
$cleanup-zombies

# 에이전트 모니터링 (3분 간격)
bash scripts/agent_monitor.sh <task_id> [max_checks=5] [interval=180]
$monitor <task_id>

# Pontail 플러그인 설치
bash scripts/pontail_setup.sh
$pontail

# CI/CD 테스트-픽스 루프
bash scripts/test_fix_pipeline.sh

# 파이프라인 테스트 (yfinance 우회)
bash scripts/run_pipeline_test.sh

# 전체 파이프라인 실행
bash scripts/full_pipeline_dd.sh

# 전체 테스트 실행
python -m pytest tests/ -v
python -m pytest services/xgboost-ml/tests/ -v
python -m pytest tests/test_etl/ -v
```
