# Data Layer Split — Architecture Design Document

> **Status:** Draft v1.0  
> **Scope:** Data ingestion layer decomposition: split monolith data collectors into Market Data and Alternative Data services  
> **Target:** Eliminate mixed-responsibility services, enable independent scaling, improve fault isolation  

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [Target Architecture — Market Data Service](#2-target-architecture--market-data-service)
3. [Target Architecture — Alternative Data Service](#3-target-architecture--alternative-data-service)
4. [Migration Strategy](#4-migration-strategy)
5. [Service Interface Contract](#5-service-interface-contract)
6. [Risk Mitigation](#6-risk-mitigation)

---

## 1. Current State Analysis

### 1.1 Service Responsibilities (As-Is)

The existing system has **4 data collection services** with overlapping and poorly-bounded responsibilities:

| Service | Market Data | Alternative Data | Problem |
|---------|-------------|------------------|---------|
| `yfinance-collector` | OHLCV prices, futures/options, foreign/institutional supply, US indices | ECOS macro data, DART financial statements | Single service handles both real-time market data and quarterly macro/financial data with vastly different collection schedules |
| `news-analyzer` | — | RSS sentiment, Naver community, DeepSeek analysis, DART disclosures | Correctly scoped to alternative data but also manages DART disclosures that yfinance-collector duplicates |
| `krx-collector` | — | Short selling, program trading, derivatives data | Standalone service with no clear boundary — data types overlap with what yfinance-collector's derivatives module collects |
| `economic-calendar` | — | FOMC schedules, earnings dates, dividend dates, economic indicators | Standalone service for what is conceptually a calendar/discovery function tightly coupled with macro data |

### 1.2 Data Flow Confusion

```
┌──────────────────────────────────────────────────────────┐
│                    yfinance-collector                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐  │
│  │  Price   │ │  Supply  │ │  Macro   │ │ Financial  │  │
│  │ (OHLCV)  │ │ (F/I/I)  │ │ (ECOS)   │ │ (DART)     │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘  │
│       │             │           │              │          │
│       ▼             ▼           ▼              ▼          │
│  market_data  foreign_inst  macro_indicators  fin_stat   │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                     news-analyzer                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐  │
│  │   RSS    │ │Community │ │  DART   │ │ DeepSeek   │  │
│  │  Feeds   │ │  Naver   │ │Disclosure│ │ Sentiment  │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘  │
│       │             │           │              │          │
│       ▼             ▼           ▼              ▼          │
│  news_analysis  stock_sentiment  Neo4j Disclosure  │      │
└──────────────────────────────────────────────────────────┘
```

### 1.3 Issues with Current Architecture

1. **Coupled lifecycles** — yfinance-collector restarts if any module fails (macro module outage blocks price collection)
2. **No independent scaling** — Cannot scale price collection separately from macro collection
3. **Duplicate DART access** — Both yfinance-collector (financial statements) and news-analyzer (disclosures) call DART API with different endpoints
4. **Mixed storage patterns** — yfinance-collector writes to `market_data` AND `macro_indicators` AND `financial_statements` with no ownership contract
5. **Scheduling mismatch** — OHLCV runs every 6h, US indices at 08:30 KST, macro data weekly — all controlled by a single scheduler

---

## 2. Target Architecture — Market Data Service

### 2.1 Purpose

Single-responsibility service for **time-sensitive market data** that powers:
- OHLCV price series (stock-vectorizer feature engineering)
- Foreign/institutional supply trends (market_features)
- Futures/options pricing (market_features — derivatives)
- US market reference data (macro_features — global context)

### 2.2 Service Location

```
services/market-data-ingestion/
├── app/
│   ├── collectors/
│   │   ├── price_collector.py        # KOSPI/KOSDAQ OHLCV
│   │   ├── supply_collector.py       # Foreign/institutional/individual
│   │   ├── derivatives_collector.py  # KOSPI200 futures, options
│   │   ├── us_indices_collector.py   # S&P500, NASDAQ, VIX, SOX
│   │   └── stock_list_collector.py   # Stock master data refresh
│   ├── processors/
│   │   ├── data_cleaner.py
│   │   └── technical_indicators.py   # Basic calc if needed downstream
│   ├── storage/
│   │   ├── postgres.py
│   │   └── redis.py
│   ├── config.py
│   └── main.py                       # Scheduler + dispatch
├── tests/
├── Dockerfile
└── requirements.txt
```

### 2.3 Data Sources & Collection Schedule

| Collector | Data Source | Data Types | Schedule |
|-----------|-------------|------------|----------|
| `price_collector` | yfinance (yfinance.Ticker) | OHLCV, volume, trading_value for KOSPI/KOSDAQ stocks | Every 6 hours (00:00, 06:00, 12:00, 18:00 KST) |
| `supply_collector` | yfinance (yfinance.Ticker) | Foreign net buy, institution net buy, individual net buy | Daily at 18:00 KST (after market close) |
| `derivatives_collector` | yfinance (yfinance.Ticker) | KOSPI200 futures price, options volume, basis, put/call ratio | Every 6 hours |
| `us_indices_collector` | yfinance (yfinance.Ticker) | ^GSPC (S&P500), ^IXIC (NASDAQ), ^VIX, SOX (SOX index) | Daily at 08:30 KST (US close) |
| `stock_list_collector` | yfinance (yfinance.Ticker) | Stock master: code, name, market, sector, industry, market_cap | Weekly (Sunday 22:00 KST) |

### 2.4 Storage Targets

| PostgreSQL Table | Written By | Read By | Columns |
|-----------------|------------|---------|---------|
| `stocks` | `stock_list_collector` | All services | stock_code, stock_name, market, sector, industry, market_cap |
| `market_data` | `price_collector` | stock-vectorizer, xgboost-ml, api-gateway | stock_code, trade_date, open/high/low/close, volume, trading_value |
| `foreign_institutional` | `supply_collector` | stock-vectorizer, xgboost-ml | stock_code, trade_date, foreign_net_buy, institution_net_buy, individual_net_buy |
| `futures_options` | `derivatives_collector` | xgboost-ml | trade_date, futures_price, options_volume, basis, options_put_call_ratio |
| `us_market_data` | `us_indices_collector` | xgboost-ml | index_name, trade_date, close_price, change_pct |

### 2.5 Redis Streams (Published)

| Stream Name | Collector | Message Format | Consumers |
|-------------|-----------|----------------|-----------|
| `market:price:collected` | `price_collector` | `{stock_code, trade_date, close}` | stock-vectorizer (invalidation) |
| `market:us:collected` | `us_indices_collector` | `{index, value, date}` | xgboost-ml (feature recompute trigger) |
| `market:ready` | All collectors | `{type, timestamp}` | stock-vectorizer (batch embedding trigger) |

### 2.6 Dependencies

- **Runtime:** postgres (stock_network), redis (stock_network)
- **External:** Yahoo Finance API (via yfinance, no API key required)
- **Python packages:** yfinance, pandas, numpy, psycopg2-binary, redis, pydantic, python-dotenv, schedule

### 2.7 Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 -r requirements.txt

COPY . .

CMD ["python", "-m", "app.main"]
```

### 2.8 Requirements

```
yfinance==0.2.54
pandas==2.2.3
numpy==2.2.4
psycopg2-binary==2.9.10
redis==5.2.1
pydantic==2.10.4
python-dotenv==1.0.1
schedule==1.2.2
```

---

## 3. Target Architecture — Alternative Data Service

### 3.1 Purpose

Single-responsibility service for **non-price alternative data** that powers:
- Sentiment analysis pipeline (news-analyzer replacement — stock_sentiment, news_analysis)
- Macro-economic indicators (ECOS replacement in yfinance-collector)
- Korean financial disclosures (DART — financial_statements, Neo4j Disclosure nodes)
- Market event calendar (economic-calendar replacement)
- KRX market statistics (krx-collector replacement)

### 3.2 Service Location

```
services/alternative-data-ingestion/
├── app/
│   ├── collectors/
│   │   ├── sentiment/
│   │   │   ├── rss_collector.py        # 5 Korean financial news RSS
│   │   │   ├── community_collector.py   # Naver cafe/blog/cafe search
│   │   │   └── dart_disclosure.py      # DART disclosures (not financial statements)
│   │   ├── macro/
│   │   │   ├── ecos_collector.py       # ECOS API (9 indicators)
│   │   │   ├── krx_short_selling.py    # KRX short selling data
│   │   │   ├── krx_program_trading.py  # KRX program trading data
│   │   │   └── krx_derivatives.py      # KRX derivatives statistics
│   │   ├── financial/
│   │   │   └── dart_financials.py      # DART financial statements
│   │   └── calendar/
│   │       ├── fomc_collector.py       # FOMC schedule
│   │       ├── earnings_collector.py   # Earnings dates
│   │       ├── dividend_collector.py   # Dividend dates
│   │       └── economic_indicator.py   # Economic indicator calendar
│   ├── analyzers/
│   │   └── deepseek_analyzer.py        # DeepSeek sentiment + authenticity
│   ├── storage/
│   │   ├── postgres.py
│   │   ├── neo4j.py
│   │   └── redis.py
│   ├── data_quality/
│   │   └── validator.py                # Cross-source validation
│   ├── config.py
│   └── main.py                         # Scheduler + dispatch
├── tests/
├── Dockerfile
└── requirements.txt
```

### 3.3 Internal Module Architecture

```
alternative-data-ingestion/
│
├── sentiment-collector  (sub-module, runs every 30 min)
│   ├── RSS feeds:        etnews, hankyung, seoulfn, asiae, MTN
│   ├── Naver community:  cafe/blog search by stock ticker
│   ├── DART disclosures: 감사보고서, 주요사항보고서
│   └── DeepSeek:         sentiment_score, authenticity_score, embedding
│       Storage:          news_analysis, stock_sentiment, Neo4j Disclosure
│
├── macro-collector  (sub-module, runs daily at 18:00 KST)
│   ├── ECOS API:         base_rate, CPI, PPI, USD/KRW, WTI,
│   │                     treasury_3y, credit_spread, corp_bond_3y,
│   │                     money_supply, industrial_production
│   ├── KRX short selling: stock_code, trade_date, short_volume, ratio
│   ├── KRX program trading: buy_volume, sell_volume, net, trade_date
│   └── KRX derivatives:  futures_price, options_volume, basis, PCR
│       Storage:          macro_indicators, krx_short_selling, krx_program_trading
│
├── disclosure-collector  (sub-module, runs daily at 20:00 KST)
│   ├── DART financials:  revenue, operating_profit, net_income,
│   │                     PER, PBR, ROE, debt_ratio
│   └── DART disclosures: 신규공시, 주요공시 (not financials)
│       Storage:          financial_statements, Neo4j Disclosure
│
└── calendar-collector  (sub-module, runs daily at 06:00 KST)
    ├── FOMC schedule:    date, event_type (meeting/minutes/speech)
    ├── Earnings dates:   stock_code, fiscal_quarter, expected_date
    ├── Dividend dates:   stock_code, ex_date, record_date, pay_date
    └── Economic calendar: indicator, date, previous, forecast
        Storage:          economic_events
```

### 3.4 ECOS Indicator Mapping

| ECOS Stat Code | Indicator | Unit | Table Column |
|----------------|-----------|------|-------------|
| `A` | Base Rate (한국은행 기준금리) | % | indicator_name='base_rate' |
| `B` | CPI (소비자물가지수) | index | indicator_name='cpi' |
| `C` | PPI (생산자물가지수) | index | indicator_name='ppi' |
| `D` | USD/KRW Exchange Rate | KRW | indicator_name='usd_krw' |
| `E` | WTI Crude Oil | USD/barrel | indicator_name='wti_crude' |
| `F` | Treasury 3Y (국고채 3년) | % | indicator_name='treasury_3y' |
| `G` | Credit Spread (회사채-국고채) | bp | indicator_name='credit_spread' |
| `H` | Corporate Bond 3Y (회사채 3년) | % | indicator_name='corp_bond_3y' |
| `I` | Industrial Production (산업생산) | index | indicator_name='industrial_production' |

### 3.5 Collection Schedule

| Module | Schedule | Rationale |
|--------|----------|-----------|
| `sentiment-collector` | Every 30 minutes (00, 30) | Market hours require near-real-time sentiment |
| `macro-collector` | Daily at 18:00 KST | ECOS publishes once daily; KRX data after market close |
| `disclosure-collector` | Daily at 20:00 KST | DART filings settle by evening |
| `calendar-collector` | Daily at 06:00 KST | Calendar data is static, once-daily refresh sufficient |

### 3.6 Storage Targets

#### PostgreSQL Tables

| Table | Written By | Read By | Primary Key |
|-------|------------|---------|-------------|
| `news_analysis` | sentiment-collector | stock-vectorizer, api-gateway | (source, published_at, title hash) |
| `news_analysis` embedding | sentiment-collector | stock-vectorizer | n/a (pgvector column) |
| `stock_sentiment` | sentiment-collector | xgboost-ml, api-gateway | (stock_code, analysis_date) |
| `macro_indicators` | macro-collector | xgboost-ml, api-gateway | (indicator_name, date) |
| `financial_statements` | disclosure-collector | stock-vectorizer, xgboost-ml | (stock_code, report_date) |
| `economic_events` | calendar-collector | strategy-agents (event trigger) | (event_type, event_date) |
| `krx_short_selling` | macro-collector | xgboost-ml | (stock_code, trade_date) |
| `krx_program_trading` | macro-collector | xgboost-ml | (trade_date) |

#### Neo4j Graph

| Node/Relation | Managed By | Description |
|---------------|------------|-------------|
| `(Disclosure)` node | disclosure-collector | Create/update disclosure nodes |
| `(Stock)-[:FILED]->(Disclosure)` | disclosure-collector | Link disclosure to stock |
| `(Disclosure)-[:RELATES_TO]->(Sector)` | disclosure-collector | Link disclosure to sector |
| `(MacroIndicator)` node | macro-collector | Update indicator values |

#### Redis Streams (Published)

| Stream Name | Module | Message Format | Consumers |
|-------------|--------|----------------|-----------|
| `sentiment:ready` | sentiment-collector | `{stock_code, avg_sentiment, date}` | stock-vectorizer, xgboost-ml |
| `macro:updated` | macro-collector | `{indicator, value, date}` | xgboost-ml |
| `disclosure:filed` | disclosure-collector | `{stock_code, disclosure_id, report_type}` | stock-vectorizer, strategy-agents |
| `calendar:event` | calendar-collector | `{event_type, date, description}` | strategy-agents |

### 3.7 Dependencies

- **Runtime:** postgres (stock_network), neo4j (stock_network), redis (stock_network)
- **External APIs:** DeepSeek (via openai-compatible API), ECOS (한국은행), DART (금융감독원), KRX (한국거래소)
- **Python packages:** openai, feedparser, beautifulsoup4, lxml, psycopg2-binary, neo4j, redis, pydantic, python-dotenv, tenacity, schedule, pyyaml, pandas, numpy, prometheus_client

### 3.8 Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 -r requirements.txt

COPY . .

CMD ["python", "-m", "app.main"]
```

### 3.9 Requirements

```
openai==1.65.0
feedparser==6.0.11
psycopg2-binary==2.9.10
neo4j==5.27.0
redis==5.2.1
pydantic==2.10.4
python-dotenv==1.0.1
beautifulsoup4==4.13.3
lxml==5.3.1
tenacity==9.0.0
schedule==1.2.2
pyyaml==6.0.2
prometheus_client>=0.14.1
pandas==2.2.3
numpy==2.2.4
```

---

## 4. Migration Strategy

### 4.1 Phase Map

```
Phase A ──────► Phase B ──────► Phase C ──────► Phase D
(Create)        (Route)         (Verify)         (Decommission)

Weeks 1-2       Weeks 3-4       Weeks 5-6        Weeks 7-8
```

### 4.2 Phase A: Create New Services (Weeks 1-2)

**Objective:** Deploy new services alongside existing ones with dual-write disabled. Validate basic connectivity and data collection.

```
┌────────────────────────┐     ┌────────────────────────┐
│   Old (running)        │     │   New (running)        │
│                        │     │                        │
│ yfinance-collector ───►│ PG  │ market-data-ingestion ─►│ PG
│ news-analyzer ────────►│     │ alternative-data-ingestion ─►│ PG, Neo4j
│ krx-collector ────────►│     │                        │
│ economic-calendar ────►│     │                        │
└────────────────────────┘     └────────────────────────┘
```

**Tasks:**
1. Create `services/market-data-ingestion/` with all 5 collectors
2. Create `services/alternative-data-ingestion/` with all 4 sub-modules
3. Add new service definitions to `docker-compose.yml` (initially `replicas: 0` or `profiles: ["migration"]`)
4. Run new services in `dry-run` mode (log what would be collected, no DB writes)
5. Verify data source connectivity (yfinance, ECOS, DART, KRX, RSS, DeepSeek)
6. **Dual-write not yet active** — old services remain the sole producers

**Exit Criteria:** All new services start without errors, log successful API calls, produce no DB writes (dry-run clean).

### 4.3 Phase B: Route New Data to New Services (Weeks 3-4)

**Objective:** Activate data collection in new services. Old services continue running. Both paths write to the same DB tables.

```
┌────────────────────────┐     ┌────────────────────────┐
│   Old (running)        │     │   New (active)         │
│                        │     │                        │
│ yfinance-collector ───►├──┐  │ market-data-ingestion ─►├──┐
│ news-analyzer ────────►├──┤  │ alternative-data-ingestion►├──┤ PG
│ krx-collector ────────►├──┤  │                        │  │
│ economic-calendar ────►├──┘  │                        │  │
└────────────────────────┘     └────────────────────────┘  │
         ┌──────────────────────────────────────────────────┘
         ▼
    Dual-write active: both paths insert into same tables
```

**Tasks:**
1. Enable `dual-write` mode in both old and new services
2. Add `source` column to critical tables (market_data, macro_indicators, etc.) with values `'legacy'` or `'v2'`
3. Deploy new services as active containers in docker-compose
4. Monitor Redis streams from both old and new services
5. Keep `krx-collector` and `economic-calendar` running — their data is folded into alternative-data-ingestion

**Exit Criteria:** All data types are being collected by the new services. No data gaps. Redis streams show both old and new producers working.

### 4.4 Phase C: Verify Both Paths Produce Identical Results (Weeks 5-6)

**Objective:** Quantitative verification that new services produce equivalent or better data than old services.

```
┌──────────────────────────────────────────────────────────┐
│                    Verification Queries                    │
│                                                           │
│  SELECT md_old.close_price, md_new.close_price            │
│  FROM market_data_old md_old                              │
│  JOIN market_data_new md_new                              │
│    ON md_old.stock_code = md_new.stock_code               │
│   AND md_old.trade_date = md_new.trade_date               │
│  WHERE ABS(md_old.close_price - md_new.close_price) > 0.01│
│                                                           │
│  → ZERO MISMATCHES = PASS                                 │
└──────────────────────────────────────────────────────────┘
```

**Verification Matrix:**

| Data Type | Old Service | New Service | Comparison Method | Tolerance |
|-----------|-------------|-------------|-------------------|-----------|
| OHLCV prices | yfinance-collector | market-data-ingestion | Row-by-row diff | 0.01% |
| Foreign supply | yfinance-collector | market-data-ingestion | Row-by-row diff | 0.1% |
| Futures/options | yfinance-collector | market-data-ingestion | Full outer join | 0.1% |
| US indices | yfinance-collector | market-data-ingestion | Date-by-date compare | 0.01% |
| Macro indicators | yfinance-collector | alternative (macro) | Indicator-by-date | 0.0% (same source) |
| Financial statements | yfinance-collector | alternative (disclosure) | Stock-report compare | 0.0% (same source) |
| DART disclosures | news-analyzer | alternative (sentiment) | ID match | exact |
| Sentiment scores | news-analyzer | alternative (sentiment) | Score correlation >0.95 | statistical |
| KRX short selling | krx-collector | alternative (macro) | Row-by-row diff | 0.0% |
| KRX program trading | krx-collector | alternative (macro) | Row-by-row diff | 0.0% |
| Economic calendar | economic-calendar | alternative (calendar) | Event-by-event | exact |

**Automation:**
```python
# scripts/validate_data_split.py
def validate_market_data():
    old = query("SELECT * FROM market_data WHERE source='legacy' AND date=CURRENT_DATE")
    new = query("SELECT * FROM market_data WHERE source='v2' AND date=CURRENT_DATE")
    mismatches = compare(old, new, tolerance=0.0001)
    return len(mismatches) == 0

def validate_all():
    checks = [
        validate_market_data(),
        validate_foreign_institutional(),
        validate_macro_indicators(),
        validate_financial_statements(),
        validate_sentiment(),
        validate_krx(),
    ]
    return all(checks)
```

**Exit Criteria:** All verification checks pass for 7 consecutive days. Zero data quality incidents.

### 4.5 Phase D: Decommission Old Services (Weeks 7-8)

**Objective:** Remove old services. Route all consumers to new service producers.

```
┌────────────────────────┐     ┌────────────────────────┐
│   Old (stopped)        │     │   New (sole producers) │
│                        │     │                        │
│ yfinance-collector ───►│ (x) │ market-data-ingestion ─►├──┐
│ news-analyzer ────────►│ (x) │ alternative-data-ingestion►├──┤ PG/Neo4j/Redis
│ krx-collector ────────►│ (x) │                        │  │
│ economic-calendar ────►│ (x) │                        │  │
└────────────────────────┘     └────────────────────────┘  │
         ┌──────────────────────────────────────────────────┘
         ▼
    Sole-write active: only new services write data
```

**Tasks:**
1. Update all downstream consumers to read from new service data sources
   - `stock-vectorizer`: Already reads from `market_data`, `news_analysis`, `stock_sentiment`, `financial_statements` — no change needed
   - `xgboost-ml`: Already reads from all PG tables — no change needed
   - `strategy-agents`: May need Redis stream consumer group updates
   - `api-gateway`: No change (reads from PG, not from collectors)
2. Stop old services (`docker-compose rm -s yfinance-collector news-analyzer krx-collector economic-calendar`)
3. Remove old service definitions from `docker-compose.yml`
4. Remove old volumes and data directories (after 30-day grace period)
5. Update documentation, README, and architecture diagrams
6. Add post-deployment monitoring alerts for new services

**Exit Criteria:** Old containers stopped and removed. System runs on only `market-data-ingestion` and `alternative-data-ingestion`. All consumers function normally.

---

## 5. Service Interface Contract

### 5.1 PostgreSQL Table Ownership

Table ownership defines which service is **authoritative** for writing to each table. Read access is unrestricted.

| Table | Owner | Old Owner(s) | INSERT/UPDATE | DELETE |
|-------|-------|--------------|---------------|--------|
| `stocks` | market-data-ingestion | yfinance-collector | market-data-ingestion | market-data-ingestion |
| `market_data` | market-data-ingestion | yfinance-collector | market-data-ingestion | market-data-ingestion |
| `foreign_institutional` | market-data-ingestion | yfinance-collector | market-data-ingestion | market-data-ingestion |
| `futures_options` | market-data-ingestion | yfinance-collector | market-data-ingestion | market-data-ingestion |
| `us_market_data` (new) | market-data-ingestion | yfinance-collector (via macro) | market-data-ingestion | market-data-ingestion |
| `news_analysis` | alternative-data-ingestion | news-analyzer | alt-data-ingestion | alt-data-ingestion |
| `stock_sentiment` | alternative-data-ingestion | news-analyzer | alt-data-ingestion | alt-data-ingestion |
| `macro_indicators` | alternative-data-ingestion | yfinance-collector | alt-data-ingestion | alt-data-ingestion |
| `financial_statements` | alternative-data-ingestion | yfinance-collector | alt-data-ingestion | alt-data-ingestion |
| `economic_events` (new) | alternative-data-ingestion | economic-calendar | alt-data-ingestion | alt-data-ingestion |
| `krx_short_selling` (new) | alternative-data-ingestion | krx-collector | alt-data-ingestion | alt-data-ingestion |
| `krx_program_trading` (new) | alternative-data-ingestion | krx-collector | alt-data-ingestion | alt-data-ingestion |
| `ml_predictions` | xgboost-ml (unchanged) | xgboost-ml | xgboost-ml | xgboost-ml |
| `stock_vectors` | stock-vectorizer (unchanged) | stock-vectorizer | stock-vectorizer | stock-vectorizer |
| `trade_orders` | trade-executor (unchanged) | trade-executor | trade-executor | trade-executor |
| `positions` | trade-executor (unchanged) | trade-executor | trade-executor | trade-executor |
| `strategy_config` | strategy-agents (unchanged) | strategy-agents | strategy-agents | strategy-agents |
| `risk_management` | strategy-agents (unchanged) | strategy-agents | strategy-agents | strategy-agents |

**Rule:** A table MUST have exactly one owner. `ON CONFLICT` handling (upsert) is the owner's responsibility. Readers must never write to a table they do not own.

### 5.2 New Tables to Create

```sql
-- US market indices (separate from macro_indicators for temporal granularity)
CREATE TABLE IF NOT EXISTS us_market_data (
    id SERIAL PRIMARY KEY,
    index_name VARCHAR(20) NOT NULL,         -- 'SP500', 'NASDAQ', 'VIX', 'SOX'
    trade_date DATE NOT NULL,
    close_price DECIMAL(20,4),
    change_pct DECIMAL(10,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(index_name, trade_date)
);

-- Economic calendar events
CREATE TABLE IF NOT EXISTS economic_events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,         -- 'FOMC', 'EARNINGS', 'DIVIDEND', 'ECONOMIC'
    event_date DATE NOT NULL,
    stock_code VARCHAR(10),                   -- NULL for macro events
    description TEXT,
    previous_value DECIMAL(20,4),
    forecast_value DECIMAL(20,4),
    actual_value DECIMAL(20,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- KRX short selling
CREATE TABLE IF NOT EXISTS krx_short_selling (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    trade_date DATE NOT NULL,
    short_volume BIGINT,
    short_ratio DECIMAL(10,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, trade_date)
);

-- KRX program trading
CREATE TABLE IF NOT EXISTS krx_program_trading (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL UNIQUE,
    buy_volume BIGINT,
    sell_volume BIGINT,
    net_volume BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5.3 Redis Stream Protocol

Each stream uses a consistent message envelope:

```json
{
  "event_id": "uuid-v4",
  "source": "market-data-ingestion|alternative-data-ingestion",
  "source_version": "1.0",
  "timestamp": "2026-07-30T10:00:00+09:00",
  "payload": { /* type-specific fields */ }
}
```

| Stream Name | Published By | Key Fields in Payload | Consumer Group |
|-------------|-------------|----------------------|----------------|
| `market:price:collected` | market-data-ingestion | `stock_code, trade_date, close, volume` | `cg:vectorizer` |
| `market:us:collected` | market-data-ingestion | `index_name, price, date` | `cg:xgboost` |
| `market:ready` | market-data-ingestion | `type: "daily" | "intraday", date` | `cg:vectorizer` |
| `sentiment:ready` | alternative-data-ingestion | `stock_code, avg_sentiment, date` | `cg:vectorizer`, `cg:xgboost` |
| `macro:updated` | alternative-data-ingestion | `indicator, value, date` | `cg:xgboost` |
| `disclosure:filed` | alternative-data-ingestion | `stock_code, disclosure_id, report_type` | `cg:vectorizer` |
| `calendar:event` | alternative-data-ingestion | `event_type, date, description` | `cg:strategy` |

**Stream lifecycle:**
- Each stream has **one producer** (no concurrent writers to avoid message ordering issues)
- Multiple consumer groups can read the same stream independently
- Messages are trimmed with `MAXLEN ~ 10000` to prevent unbounded growth
- Dead-letter: messages that fail after 3 retries go to `{stream}:dead` via `XADD`

### 5.4 Neo4j Update Responsibility

| Node/Relation | Managed By | Update Frequency | Cypher Pattern |
|---------------|------------|-----------------|----------------|
| `(Disclosure)` | alternative-data-ingestion (disclosure-collector) | Daily | `MERGE (d:Disclosure {disclosure_id: $id}) ON CREATE SET ...` |
| `(Stock)-[:FILED]->(Disclosure)` | alternative-data-ingestion (disclosure-collector) | Daily | `MATCH (s:Stock {code: $code}), (d:Disclosure {disclosure_id: $id}) MERGE (s)-[:FILED]->(d)` |
| `(Disclosure)-[:RELATES_TO]->(Sector)` | alternative-data-ingestion (disclosure-collector) | Daily | `MERGE (d)-[:RELATES_TO]->(sect)` |
| `(MacroIndicator)` | alternative-data-ingestion (macro-collector) | Daily | `MERGE (m:MacroIndicator {name: $name}) SET m.latest_value = $val` |

**Note:** `stock-vectorizer` and `strategy-agents` are **readers only** for Neo4j. They query the graph but never mutate it.

---

## 6. Risk Mitigation

### 6.1 Dual-Write Consistency Checking

During Phase C, every write to a storage target must be independently verified:

```python
# Consistency check flow
def dual_write_and_verify(collector_func, table, primary_key):
    # 1. Collect data from source
    data = collector_func()

    # 2. Write via new service path
    new_hash = write_v2(data)

    # 3. Write via old service path (simulated — read from old path)
    old_hash = read_from_legacy(primary_key)

    # 4. Compare
    if new_hash != old_hash:
        alert_consistency_failure(table, primary_key, old_hash, new_hash)
        return False
    return True
```

**Alerting threshold:** Any mismatch >= 1 row triggers a P1 alert (Slack + email). Cumulative mismatch rate must remain below 0.01% of total rows.

### 6.2 Rollback Plan

If the new service fails at any phase, the rollback procedure is:

| Phase | Trigger | Rollback Action | Recovery Time |
|-------|---------|-----------------|---------------|
| Phase A | New service fails dry-run | Fix and redeploy; no data impact | Hours |
| Phase B | Dual-write detects data corruption | Stop new service containers; old services resume sole-write | < 5 min |
| Phase C | Verification check fails > 5% | Disable new service writes; keep old services as sole producers | < 5 min |
| Phase D | New service fails after decommission | `docker-compose up -d` for old services from preserved config | < 10 min |

**Rollback script structure:**

```bash
# scripts/rollback_data_split.sh
#!/bin/bash
# Phase: $1 (A|B|C|D)

rollback_phase_b() {
    echo "Rolling back Phase B..."
    docker-compose stop market-data-ingestion alternative-data-ingestion
    docker-compose rm -f market-data-ingestion alternative-data-ingestion
    docker-compose up -d yfinance-collector news-analyzer krx-collector economic-calendar
    echo "Rollback complete. Old services restored as sole producers."
}

rollback_phase_d() {
    echo "Rolling back Phase D..."
    # Restore old services from preserved docker-compose.legacy.yml
    docker-compose -f docker-compose.legacy.yml up -d
    docker-compose stop market-data-ingestion alternative-data-ingestion
    echo "Rollback complete. Verify data pipeline health."
}

case $1 in
    B) rollback_phase_b ;;
    D) rollback_phase_d ;;
    *) echo "No rollback needed for Phase $1" ;;
esac
```

### 6.3 Data Validation Checks

Automated data quality checks run **hourly** during Phase C and **daily** post-migration:

| Check | Query | Expected |
|-------|-------|----------|
| Row count parity | `SELECT COUNT(*) FROM market_data WHERE source='legacy' UNION ALL SELECT COUNT(*) WHERE source='v2'` | Equal row counts |
| Null ratio | `SELECT stock_code, COUNT(*) as nulls FROM market_data WHERE close_price IS NULL GROUP BY stock_code HAVING COUNT(*) > 0` | Zero rows |
| Freshness | `SELECT MAX(trade_date) FROM market_data WHERE source='v2'` | Today (KST) |
| Duplicate check | `SELECT stock_code, trade_date, COUNT(*) FROM market_data GROUP BY stock_code, trade_date HAVING COUNT(*) > 1` | Zero rows (peer source dedup) |
| Sentiment range | `SELECT MIN(avg_sentiment), MAX(avg_sentiment) FROM stock_sentiment` | [-1.0, 1.0] |
| Foreign key integrity | `SELECT md.stock_code FROM market_data md LEFT JOIN stocks s ON md.stock_code = s.stock_code WHERE s.stock_code IS NULL` | Zero rows |

### 6.4 Runbook: New Service Failure

**Symptom:** `market-data-ingestion` container exits or data collection stops.

**Impact:** Market data (OHLCV, supply, derivatives, US indices) stops updating within 6 hours. ML features using market data degrade within 1 day.

**Immediate response:**
1. Check container status: `docker ps | grep market-data-ingestion`
2. Check logs: `docker-compose logs --tail=100 market-data-ingestion`
3. If error in dependencies (PostgreSQL, Redis): restart dependencies first
4. If error in collection logic: restart with `docker-compose restart market-data-ingestion`
5. If restart fails: escalate to Phase B rollback

**Root cause analysis:**
```bash
# Common failure modes
docker-compose logs market-data-ingestion | grep -i error
docker-compose logs market-data-ingestion | grep -i traceback
docker inspect market-data-ingestion --format='{{.State.Health.Status}}'
```

**Post-recovery verification:**
```bash
# Verify data flowing again
redis-cli -a $REDIS_PASSWORD XLEN market:price:collected
psql -U $POSTGRES_USER -d $POSTGRES_DB -c \
  "SELECT MAX(trade_date) FROM market_data;"
```

### 6.5 Cron-Triggered Reconciliation

A daily cron job (or scheduled container) runs full reconciliation:

```bash
# docker-compose.yml fragment
reconciliation-worker:
  build: ./services/reconciliation
  container_name: stock_reconciliation
  env_file: .env
  command: python -m app.reconcile
  depends_on:
    - postgres
  profiles: ["migration"]
```

This worker:
- Compares row counts between `source='legacy'` and `source='v2'` for all tables
- Reports discrepancies to a `reconciliation_log` table
- If discrepancy persists > 24 hours, auto-escalates to rollback procedure
- Runs daily at 02:00 KST (low-traffic window)

---

## Appendix A: Docker Compose Changes

### A.1 New Service Definitions

```yaml
market-data-ingestion:
  build:
    context: ./services/market-data-ingestion
    dockerfile: Dockerfile
  container_name: stock_market_data_ingestion
  env_file: .env
  volumes:
    - ./services/market-data-ingestion:/app
    - ./data/market:/app/data
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy
  networks:
    - stock_network
  restart: unless-stopped

alternative-data-ingestion:
  build:
    context: ./services/alternative-data-ingestion
    dockerfile: Dockerfile
  container_name: stock_alt_data_ingestion
  env_file: .env
  volumes:
    - ./services/alternative-data-ingestion:/app
    - ./data/disclosures:/app/data
  depends_on:
    postgres:
      condition: service_healthy
    neo4j:
      condition: service_healthy
    redis:
      condition: service_healthy
  networks:
    - stock_network
  restart: unless-stopped
```

### A.2 Volume Changes

No new volumes required. Both new services use existing `stock_network` and mount host volumes for data cache.

---

## Appendix B: Environment Variables

New environment variables required by the split (in addition to existing 10):

| Variable | Used By | Description |
|----------|---------|-------------|
| `SOURCE_LABEL` | Both new services | `'legacy'` during dual-write, `'v2'` eventually |
| `DRY_RUN` | Both new services | `'true'` during Phase A to prevent DB writes |
| `DUAL_WRITE` | Both new services | `'true'` during Phase B-C to enable dual-write mode |
| `RECONCILIATION_ENABLED` | reconciliation-worker | `'true'` during Phase C |

All existing environment variables (`DEEPSEEK_API_KEY`, `DART_API_KEY`, `ECOS_API_KEY`, etc.) remain unchanged.

---

## Appendix C: Dependency Graph

```
                    ┌─────────────┐
                    │  postgres   │
                    └──────┬──────┘
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
  ┌───────────────┐ ┌───────────────┐ ┌──────────────┐
  │market-data-   │ │alternative-   │ │stock-        │
  │ingestion      │ │data-ingestion │ │vectorizer    │
  └───────┬───────┘ └───────┬───────┘ └──────┬───────┘
          │                 │                │
          └─────────────────┼────────────────┘
                            │
                            ▼
                     ┌──────────────┐
                     │  xgboost-ml  │
                     └──────┬───────┘
                            │
                            ▼
                     ┌──────────────┐
                     │  strategy-   │
                     │  agents      │
                     └──────┬───────┘
                            │
                            ▼
                     ┌──────────────┐
                     │  trade-      │
                     │  executor    │
                     └──────────────┘

Legend:
  ──►  data flow (PG read/write or Redis stream)
  - -  Redis stream (async signal)
```

The split does not change the downstream dependency graph. `stock-vectorizer`, `xgboost-ml`, and `strategy-agents` read from the same PostgreSQL tables and Neo4j graph regardless of which service wrote the data.
