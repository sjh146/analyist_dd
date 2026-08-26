# KIS 컬렉터 PLAN (kis-collector)

KRX 차단(2026-08-24~, IP 7일 제한)을 대체하는 **한국투자증권 KIS REST API** 기반
일봉+분봉 수집기. 일봉은 기존 `market_data` 테이블을 KIS로 채우고, 분봉은 신규
`minute_bars` 테이블로 저장 → **단타 30분 채점(MinutePriceProvider)** + 쌍둥이
실시간 분봉의 기반이 된다.

> 작성일: 2026-08-26 | 작업 디렉토리: `/home/dduckbeagy/analyist_dd`
> ⚠️ 실측 검증 필요 항목(§9) — 본 문서의 tr_id/필드명은 공식 가이드 기준이며,
> 최초 실전 실행 전 Hermes의 1회 실측으로 확정한다. **실제 KIS 호출은 본 구현에서
> 일절 하지 않는다.**

---

## 1. 목적 및 배경

| 항목 | 내용 |
|------|------|
| 배경 | KRX 오픈API가 2026-08-24부터 IP 7일 제한 (WAF 차단) |
| 대안 | 한국투자증권 KIS OpenAPI (`openapi.koreainvestment.com:9443`, 실전 포트) |
| 대상 | 일봉(전 종목, 기존 `market_data`) + 분봉(당일, 신규 `minute_bars`) |
| 소비처 | 단타 30분 채점(`KisMinuteProvider`), 쌍둥이 실시간 분봉(향후) |
| 운영 | KRX 차단 해제(~08/28) 후에도 **KIS가 주 소스**, krx-collector는 폴백 유지 |

---

## 2. 아키텍처 개요

`services/kis-collector/` 는 `krx-collector` 와 병렬 구조(같은 app/ 패턴)의 독립
Docker 서비스. 구성요소:

```
services/kis-collector/
├── Dockerfile                      # python:3.11-slim + curl (WAF 우회용)
├── requirements.txt                # psycopg2-binary (curl은 시스템 패키지)
└── kis_app/                        # ⚠️ 'app'이 아닌 고유 패키지명 — 호스트 테스트에서
    ├── config.py                   #   타 서비스 xgboost-ml의 app 패키지와 sys.modules 충돌 방지
    ├── utils.py                    # to_float/to_int/to_date/add_minutes (HHMMSS)
    ├── client/
    │   └── kis_client.py           # TokenManager(24h 캐시) + KisClient(curl 호출/재시도)
    ├── collectors/
    │   ├── daily_collector.py      # 일봉: inquire-daily-itemchartprice → market_data
    │   └── minute_collector.py     # 분봉: inquire-time-itemchartprice → minute_bars (페이지네이션)
    ├── storage/
    │   └── postgres_storage.py     # 유니버스 조회 + market_data/minute_bars upsert
    └── main.py                     # CLI (--job daily|minute|all, --date, --limit)
```

**호출 계층**: `main` → `collector` → `KisClient`(curl subprocess) → `TokenManager`.

- **실제 HTTP는 `subprocess curl`** — urllib/python-requests는 WAF 403 (2026-08-25
  실측). `curl_cffi` 대신 curl을 선택한 이유: Docker 이미지에서 추가 파이썬
  네이티브 의존성 없이 시스템 curl로 동일한 브라우저급 TLS 핑거프린트 확보.
- **토큰**: `TokenManager`가 메모리+파일(`data/kis/token_cache.json`) 이중 캐시.
  JWT(24h)는 발급 1분당 1회 제한(EGW00133)이므로 **캐시 후 재사용**, 만료
  임박(<5분) 또는 401(EGW00115) 시에만 재발급.

---

## 3. KIS API 레퍼런스 (공식 가이드 기준 — §9 실측 확정)

### 3.1 토큰 발급 (실전)

| 항목 | 값 |
|------|-----|
| Method/Path | `POST /oauth2/tokenP` (⚠️ `/oauth2/token` 아님 — EGW00115) |
| Base URL | `https://openapi.koreainvestment.com:9443` |
| Body | `{"grant_type":"client_credentials","appkey":"{KIS_APP_KEY}","appsecret":"{KIS_APP_SECRET}"}` |
| Response | `{"access_token":"...","token_type":"Bearer","expires_in":86400}` |
| 제한 | **1분당 1회 (EGW00133)** → 반드시 캐시(24h) 후 재사용 |

### 3.2 일봉 — 국내주식 기간별 시세(일/주/월봉)

| 항목 | 값 |
|------|-----|
| Endpoint | `GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` |
| tr_id | 실전 `HHSTC16921500` (모의와 다름 — 실전 코드 사용) |
| Header | `authorization: Bearer {token}`, `appkey`, `appsecret`, `tr_id`, `content-type: application/json; charset=utf-8` |
| Query | `AUTH=""`, `EXCD=KSS|KSQ`, `SYMB={6자리}`, `FID_INPUT_ISC=00000000`, `FID_PERIOD_DIV=D`, `FID_INPUT_DATE_1={YYYYMMDD}`, `FID_INPUT_DATE_2={YYYYMMDD}`, `FID_CNT={건수}` |
| Response | `output2[]` 일봉 배열 (아래 필드) |

`output2` 필드 → `market_data` 매핑:

| KIS 필드 | 의미 | market_data 컬럼 |
|----------|------|------------------|
| `stck_bsop_date` | 영업일자 YYYYMMDD | `trade_date` (DATE) |
| `stck_oprc` | 시가 | `open_price` |
| `stck_hgpr` | 최고가 | `high_price` |
| `stck_lwpr` | 최저가 | `low_price` |
| `stck_clpr` | 종가 | `close_price` |
| `cntg_vol` (fallback `acml_vol`) | 거래량 | `volume` |
| `acml_tr_pbmn` | 누적 거래대금(원) | `trading_value` |

- 실측 노트: 필드 스키마 불일치 시 `OPSQ2001 ERROR INPUT FIELD NOT FOUND` —
  `EXCD`는 이 API(기간별시세 계열)의 필드이며, `inquire-price`에는 없다(2026-08-25
  실측). 파서는 알 수 없는 키를 무시하되 **필수 키(`stck_bsop_date`/`stck_clpr`)
  누락 시 행을 스킵**하고 오류를 로그로 남긴다.

### 3.3 분봉 — 국내주식 시간별 시세(당일/과거)

| 항목 | 값 |
|------|-----|
| Endpoint | `GET /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` |
| tr_id | 실전 `HHSTC16922500` |
| Query | `AUTH=""`, `EXCD=KSS|KSQ`, `SYMB={6자리}`, `FID_INPUT_ISC=00000000`, `FID_INPUT_HOUR={HHMMSS}`, `FID_PERIOD_DIV=0`(1분봉), `FID_COND_MRKT_DIV_CODE=J`, `FID_COND_SCR_DIV_CODE=20181`, `FID_PCLD_INCU_YN=Y`, `FID_CNT={≤100}` |
| Response | `output2[]` 분봉 배열 — **시간 내림차순(최신 우선)** |

`output2` 필드 → `minute_bars` 매핑:

| KIS 필드 | 의미 | minute_bars 컬럼 |
|----------|------|------------------|
| `stck_bsop_date` | 영업일자 YYYYMMDD | `trade_date` (DATE) |
| `stck_cntg_hour` | 거래시간 HHMMSS | `"time"` (CHAR(6)) |
| `stck_oprc` / `stck_hgpr` / `stck_lwpr` | 시가/최고/최저 | `open/high/low_price` |
| `stck_prpr` | 현재가(= 해당 분 종가) | `close_price` |
| `cntg_vol` | 해당 분 거래량 | `volume` |
| `acml_tr_pbmn` | 누적 거래대금 | `trading_value` |

**1회 응답 건수 제한**: `FID_CNT` 최대 **100건/호출** (공식 가이드). 정규장
09:00–15:30 = 391분이므로 **4페이지 필요** (100+100+100+91). 페이지네이션:
이전 페이지 최저 시각 −1분을 다음 `FID_INPUT_HOUR`로 재요청, 배치 < FID_CNT 또는
`090000` 도달 또는 max_pages(기본 10) 방어로 종료.

---

## 4. 호출 제한 설계 (보수적)

| 구분 | 설계값 | 근거 |
|------|--------|------|
| 토큰 발급 | 캐시 24h, 남은 수명 <5분 시 재발급 | EGW00133(1분 1회) 회피 |
| 토큰 재시도 | EGW00133 시 60s 대기 후 재시도 (최대 2회) | 1분 제한 |
| 시세 호출 간격 | 기본 **3.0s + jitter 0~0.5s** (`KIS_REQUEST_DELAY`) | 공식 분당 제한(100회) 대비 여유 — 20회/분 수준 |
| 전 종목 소요 | 1765종목 × ~3.3s ≈ **~97분 (~1.6h)** | 지시서 "하루 1~2시간"과 부합 |
| 재시도 | 지수 백오프 2s→4s→8s (최대 5회, `KIS_RETRY_MAX`) | 429/5xx/EGW rate-limit 코드만 |
| 즉시 실패 | `OPSQ2001`(필드 스키마 오류) — 설정 버그이므로 재시도 없이 raise | 오류 은폐 방지 |
| 토큰 갱신 | 401/EGW00115 → 무효화 → 재발급 → **1회만** 재시도 | 무한 루프 방지 |
| dry-run | `KIS_DRY_RUN=1` → curl/DB 대신 로그만 (흐름 점검용) | 실호출 금지 원칙 |
| 크론 | 미등록 — **Hermes 담당** (§10 cron) | 지시서 제약 6 |

오류 분류 테이블 (`kis_client.py`):

| 코드 | 의미 | 처리 |
|------|------|------|
| `EGW00133` | 토큰 1분 1회 초과 | 발급 시 60s 대기+재시도 |
| `EGW00115` | 토큰 무효/만료 | 무효화→재발급→1회 재시도 |
| `OPSQ2001` | 입력 필드 오류 | 즉시 raise (설정 버그) |
| `EGW00225/EGW00123/...`, HTTP 429/5xx | 일시적 제한/서버 오류 | 백오프 재시도 |
| curl exit≠0 / 비JSON | 전송 오류 | `KisTransportError` → 재시도 분류 |

---

## 5. 스키마

### 5.1 `market_data` (기존, 그대로 재사용)

```
stock_code, trade_date, open_price, high_price, low_price, close_price,
volume, trading_value  — UNIQUE(stock_code, trade_date) (01_schema.sql)
```
KIS 일봉 → 위 컬럼에 1:1 upsert. **스키마 변경 없음.**

### 5.2 `minute_bars` (신규 — `init-scripts/postgres/06_kis_minute_bars.sql`)

```sql
CREATE TABLE IF NOT EXISTS minute_bars (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    trade_date DATE NOT NULL,
    "time" CHAR(6) NOT NULL,          -- HHMMSS (KIS stck_cntg_hour)
    open_price DECIMAL(20,4),
    high_price DECIMAL(20,4),
    low_price DECIMAL(20,4),
    close_price DECIMAL(20,4),
    volume BIGINT,
    trading_value DECIMAL(30,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (stock_code, trade_date, "time")
);
CREATE INDEX IF NOT EXISTS idx_minute_bars_stock_date ON minute_bars(stock_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_minute_bars_date ON minute_bars(trade_date);
```

- `"time"`은 SQL 예약어 비예약 키워드 — 항상 따옴표로 참조.
- **기존 DB 대응**: 컬렉터 `PostgresStorage._ensure_tables()`가 동일 DDL을
  `IF NOT EXISTS`로 실행 → init-scripts 재실행 없이 테이블 보장(비파괴).
- 일자별 인덱스(지시서) + 종목×일자 인덱스(30분 채점 조회 경로).

---

## 6. 수집 흐름

### 6.1 유니버스

`SELECT DISTINCT md.stock_code, COALESCE(s.market,'KOSPI') FROM market_data md
LEFT JOIN stocks s ON md.stock_code=s.stock_code` — **기존 market_data에 존재하는
종목 코드** (KRX 수집분 기준). market → EXCD 매핑: `KOSPI→KSS`, `KOSDAQ→KSQ`
(기타→KSS 폴백).

### 6.2 일봉 (`--job daily --date YYYYMMDD`)

1. 유니버스 로드 → 종목별 `inquire-daily-itemchartprice` (시작=종료=대상일,
   `FID_CNT=5`)
2. `output2` 파싱 → 대상일 행만 `market_data` upsert (`ON CONFLICT ... DO UPDATE`)
3. 종목 간 `KIS_REQUEST_DELAY`(기본 3s+jitter) — **반드시 천천히**
4. 개별 종목 실패는 로그+카운트 후 계속 (전체 중단 없음)

### 6.3 분봉 (`--job minute --date YYYYMMDD`)

기본은 **장 마감 후 전 종목 당일 1회** (설계 결정): 30분 창 채점은 다음날 개장
후 필요하므로 당일 분봉은 마감 후 수집으로 충분. 실시간 수집은 쌍둥이
실시간 분봉(향후)에서 별도 처리.

1. 유니버스 로드 → 종목별 `inquire-time-itemchartprice` (1분봉, `FID_CNT=100`)
2. `FID_INPUT_HOUR=153000` 시작 → **시간 내림차순 응답을 과거로 페이지네이션**
   (최저 시각−1분), `090000` 도달/배치<100/max_pages=10 시 종료
3. 대상일 + 장중(09:00–15:30) + 필수필드 있는 봉만 `minute_bars` upsert
4. 딜레이는 페이지 호출마다 동일 적용

### 6.4 실행 예시

```bash
# 일봉 (장 마감 후, 전 종목 — 약 1.5~2h)
python3 -m kis_app.main --job daily --date 20260825

# 분봉 (장 마감 후, 전 종목 당일 1회)
python3 -m kis_app.main --job minute --date 20260825

# 소규모 점검
python3 -m kis_app.main --job all --date 20260825 --limit 10

# dry-run (실호출/DB 쓰기 없음)
KIS_DRY_RUN=1 python3 -m kis_app.main --job daily --date 20260825 --limit 3
```

---

## 7. docker-compose 등록 (Hermes 적용 요청)

`docker-compose.yml`에 아래 블록 추가 (본 작업에서는 파일 수정 최소화 원칙에 따라
**PLAN에만 명시** — Hermes 검토 후 반영):

```yaml
  kis-collector:
    build:
      context: ./services/kis-collector
      dockerfile: Dockerfile
    container_name: stock_kis_collector
    env_file: .env          # KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO 등
    environment:
      - KIS_TOKEN_PATH=/app/data/kis/token_cache.json
    volumes:
      - ./services/kis-collector:/app
      - ./data:/app/data
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - stock_network
    restart: unless-stopped
    command: ["python", "-m", "kis_app.main", "--job", "all"]
```

`.env`에 추가할 키 (README 환경변수 표 갱신 요청): `KIS_APP_KEY`,
`KIS_APP_SECRET`, `KIS_ACCOUNT_NO`(=`7399634001` → CANO=`73996340`,
ACNT_PRDT_CD=`01`; 시세 API에 불필요하나 계좌 연계 시 사용).

---

## 8. 30분 채점 연동 (Phase 3)

- `scripts/day_trading_engine/minute_provider.py`에 `KisMinuteProvider` 추가:
  - DB 경로: `minute_bars`에서 `trade_date=D+1 AND "time" <= (09:00+offset)`
    ORDER BY `"time"` DESC LIMIT 1 → 종가 반환 (봉 누락 대비 `≤` 조건).
  - dict 경로: `{(code, date): price}` (DailyGapProvider와 동일 규약, DB-free 테스트).
  - 데이터 없으면 `None` → 창 미경과로 간주, 채점 제외 (기존 시맨틱 유지).
- `scripts/daytrading_performance.py`: `--provider gap|kis` 훅 추가(기본 `gap` —
  기존 동작 불변). `kis` 선택 시 `KisMinuteProvider(pg)`로 교체 → 30분 창 채점.
- `scripts/screener_score.py`: daytrading 채점은 provider 추상화를 거치지 않고
  market_data 직접 조회 — **연결부 없음 → 수정하지 않음** (지시서 §Phase3).
- `day_trading_engine/__init__.py`: `KisMinuteProvider` export 추가.

---

## 9. 한계 및 가정 (검증 필요 항목)

| # | 항목 | 상태 |
|---|------|------|
| 1 | tr_id 실전 코드 확정: 일봉 `HHSTC16921500`, 분봉 `HHSTC16922500` | 공식 가이드 기준 — **Hermes 1회 실측으로 확정 필요** |
| 2 | `output2` 필드명 (`stck_cntg_hour`, `stck_prpr`, `cntg_vol`, `acml_tr_pbmn`) | 동일 |
| 3 | 분봉 응답이 시간 내림차순 + `FID_INPUT_HOUR`=기준시각(과거로 진행) 가정 | 페이지네이션 방향 — 무한루프 가드+날짜/시간 필터로 안전, 실측 확인 필요 |
| 4 | 일봉 `cntg_vol`=당일 거래량 (fallback `acml_vol`) | 동일 |
| 5 | KRX 차단 해제 후에도 KIS가 주 소스, krx-collector는 폴백 | 운영 결정 문서화 |
| 6 | 실전 계좌 불필요(시세는 계좌 무관) — `KIS_ACCOUNT_NO`는 향후 주문/잔고용 | — |
| 7 | 장 마감 후 실행 전제 (당일분 수집). 개장 중 실행 시 당일 분봉은 미완성 | — |
| 8 | 1765종목 × 3.3s ≈ 1.6h — 지시서 예상과 부합 | — |

---

## 10. Hermes 담당 (본 작업 범위 밖)

1. docker-compose 서비스 등록 (§7 블록 적용)
2. cron/scheduler 등록 (예: `0 19 * * 1-5` 일봉, `0 19:30 * * 1-5` 분봉 — 장 마감 후)
3. tr_id/필드 실측 검증 (§9) — 1회 호출로 확정
4. `docs/KIS_컬렉터_PLAN.md` 검토 후 Git 커밋/푸시 (본 작업은 커밋 금지)

---

## 11. 테스트 전략 (전부 모킹 — 실호출 금지)

| 파일 | 커버 |
|------|------|
| `tests/test_kis_collector.py` | 토큰 캐시/재발급(1분 제한 EGW00133 모킹), 401→재발급→1회 재시도, 일봉 파싱(샘플 JSON), market_data upsert 중복 방지(ON CONFLICT), 유니버스→EXCD, 호출 딜레이 |
| `tests/test_kis_minute.py` | 분봉 파싱·시간 필터, 페이지네이션(100건 초과·max_pages 방어·배치<건수 종료), minute_bars upsert, KisMinuteProvider 30분 가격(DB/dict 경로), 데이터 없음→None |

실행: `cd /home/dduckbeagy/analyist_dd && python3 -m pytest tests/test_kis_*.py -q`
