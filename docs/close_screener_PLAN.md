# 종가스크리너 (Close-Bet Screener) 계획서

> 작성일: 2026-08-21
> 목적: 장 마감(종가) 시점에 확정된 일봉 데이터 + 수급 신호로 매수 후보를 선별하여
> **종가 매수 → 다음 거래일 매도**하는 1일 보유 단기 매매용 스크리너.
> 기존 스윙스크리너(`scripts/swing_screener.py`, ML 예측 기반)와 나란히 운영한다.

---

## 1. 전략 정의

- **컨셉**: 당일 종가가 강하게 마감(거래량 급증 + 상승 마감 + 단기 모멘텀)하고,
  공매도 부담이 낮으며, 시장 수급 레짐이 중립 이상인 종목은 다음 거래일에도
  단기 상승 모멘텀이 지속될 가능성이 있다는 관찰에 기반한 단기 배팅.
- **유니버스**: KOSDAQ 전체 — `stocks.market = 'KOSDAQ'` AND `market_data` 20거래일 이상
  (swing_screener의 `get_kosdaq_stocks`와 동일한 셀렉트 패턴 재사용).
- **운영 흐름**:
  1. 장 마감 후 `scripts/close_screener.py` 실행 → 후보 Top-N CSV 생성
  2. 후보 종목 종가 매수 (실무에서는 종가~다음날 시가 사이 체결 가정)
  3. 다음 거래일 시가 또는 종가에 매도
  4. `scripts/close_screener_performance.py`로 익일 매도 수익률/승률 검증

## 2. 실제 데이터 가용성 확인 결과 (2026-08-21, information_schema + 실데이터 조회)

계획 수립 전 DB에서 직접 확인한 결과이며, **구현은 이 실측 결과에 맞춘다**.

| 소스 | 테이블 | 단위 | 확인 결과 | 사용 여부 |
|---|---|---|---|---|
| 가격/거래량 | `market_data` | 종목별 OHLCV | 최근 2026-08-19까지, 약 98.8만 행. **단, `trading_value`는 최근 수개월 전체 NULL** (2026-07-20 이후 확인) | ✅ 핵심 (거래대금은 close×volume 근사 폴백) |
| 공매도 | `krx_short_selling` | 종목별 | 컬럼명은 `short_ratio`. 08-14까지 943종목 실데이터, **08-17~20은 `stock_code='NaN'` 플레이스홀더 행만 존재** | ✅ (숫자 코드만 필터링) |
| 외국인/기관 | `foreign_institutional` | 종목별 | **0행 — 데이터 없음** | ❌ 사용 불가 |
| 투자자 수급 | `krx_trading` | **시장 전체**(KOSPI만) | investor_type별 net_buy, ~2026-08-21 | ✅ 시장 레짐 게이트 |
| 프로그램 매매 | `krx_program_trading` | **시장 전체**(KOSPI만) | net_buy 41행. **모든 날짜에 동일 값(수집기 스냅샷 의심)** → 최근 가용일 1일 값만 사용 | ⚠️ 시장 레짐 참고 (데이터 품질 주의) |

**주요 결정(합리적 기본값)**:
1. swing_screener의 헬퍼가 참조하는 `program_trading.program_net`, `short_selling.short_selling_ratio`,
   `economic_calendar` 테이블은 **실제 스키마에 존재하지 않으므로** 그대로 재사용하지 않고,
   동일한 "소스(KRX 수급 데이터)"를 **실제 테이블명/컬럼명으로 재작성**한다.
2. 종목별 외국인/기관 수급 데이터가 비어 있으므로, 종목 단위 수급 신호는
   **공매도 비율(short_ratio)** 만 사용하고, 외국인/기관/프로그램 매매는
   **KOSPI 시장 전체 레짐 보정(±점수)** 으로 반영한다.
   (`foreign_institutional` 수집 파이프라인이 복구되면 종목별 점수로 확장할 것 — §9)

## 3. 데이터 스키마 (사용 컬럼)

```sql
market_data        (stock_code, trade_date, open_price, high_price, low_price,
                    close_price, volume, trading_value)
stocks             (stock_code, stock_name, market, sector)
krx_short_selling  (trade_date, stock_code, short_ratio)          -- 숫자 코드만
krx_trading        (trade_date, market='KOSPI', investor_type, net_buy)
                   -- investor_type IN ('Foreign','Institution')
krx_program_trading(trade_date, market='KOSPI', net_buy)
```

## 4. 시그널 규칙 (일봉 기준 — 장중 30분 추정 없음, 종가 확정값만 사용)

### 4.1 지표 정의 (종목당 최근 7거래일 윈도우, r6=당일)

| 지표 | 정의 | 비고 |
|---|---|---|
| `volume_surge` | v6 / mean(v1..v5) | 직전 5거래일 평균 대비 거래량 배수 |
| `close_strength` | (c6 − l6) / (h6 − l6) | 일봉 내 종가 위치. h==l이면 0.5(중립) |
| `ret_3d` | c6 / c3 − 1 | 3거래일 모멘텀 |
| `ret_5d` | c6 / c1 − 1 | 5거래일 모멘텀 |
| `day_change` | c6 / c5 − 1 | 당일 등락률 |
| `short_ratio` | 최근 5거래일 short_ratio 평균 | 데이터 없으면 NaN → 중립 처리 |

### 4.2 하드 필터 (후보 제외 조건)

- `거래대금 >= 3억 원` (기본값, CLI `--min-trading-value`) — 유동성 확보.
  **`trading_value`가 NULL인 최근 데이터(수집 공백)는 `close_price × volume` 근사값으로 판정**
- `close_price >= 1,000원` (CLI `--min-price`) — 동전주 제외
- `volume > 0`, 가격 결측 제외, 최근 7거래일 데이터 미달 종목 제외

### 4.3 점수 산식 (0~100 + 레짐 보정 ±5)

| 항목 | 배점 | 산식 |
|---|---|---|
| 거래량 급증 | 25 | `min(volume_surge / 3.0, 1.0) × 25` |
| 종가 강도 | 20 | `close_strength × 20` |
| 모멘텀 | 25 | `12.5 + clip(ret_3d/0.06, −1, 1)×7.5 + clip(ret_5d/0.10, −1, 1)×5` |
| 당일 상승 마감 | 10 | `clip(day_change/0.05, 0, 1) × 10` |
| 공매도 (낮을수록 가점) | 20 | NaN→10(중립), else `clamp(1 − short_ratio/0.03, 0, 1) × 20` |
| **소계** | **100** | |
| 시장 레짐 보정 | ±5 | `(외국인+기관 순매수 합 + 프로그램 순매수)` > 0 → +5, < 0 → −5, 데이터 없음/0 → 0 |

- 최종 `score = clamp(소계 + 레짐보정, 0, 100)` — 내림차순 랭킹, `--top-n`(기본 20) 선별.
- `reason` 문자열에 상위 드라이버를 사람이 읽을 수 있게 기록
  (예: `"거래량 4.2배, 종가강도 92%, 3일 +8.3%, 공매도 0.4%"`).

## 5. 매수/매도 규칙

- **매수**: 선별일(`signal_date`) 종가에 매수 (시그널이 종가 확정 후 산출되므로 이론 체결가 = 종가).
- **매도**: 다음 거래일(T+1) — 두 방식을 모두 검증:
  - **시가 매도**: `(T+1 시가 − T 종가) / T 종가 × 100`
  - **종가 매도**: `(T+1 종가 − T 종가) / T 종가 × 100`
- **리스크 운영 가정**(본 도구 범위 밖, 문서화): 종목당 자금 균등 분산(1/n),
  후보군 전체가 갭다운 국면이면 레짐 보정이 음수가 되어 선별 자체가 줄어듦.

## 6. 산출물 및 CLI

### 6.1 `scripts/close_screener.py`

```
옵션: --top-n (기본 20), --date YYYY-MM-DD (기본 = market_data 최근 거래일),
      --output PATH, --min-trading-value (기본 300000000), --min-price (기본 1000)
출력: data/reports/close_candidates_<YYYYMMDD_HHMMSS>.csv
컬럼: rank, stock_code, stock_name, sector, signal_date, close_price, score,
      volume_surge, close_strength, ret_3d_pct, ret_5d_pct, day_change_pct,
      short_ratio, reason
```

- 날짜 해석: 미지정 → `MAX(trade_date)`; 지정 → 해당 날짜 **이하**의 최대 거래일(휴장 무시).
- 가격 이력은 윈도우 함수로 종목당 최근 7행을 **쿼리 1회**에 로드 (전 종목 per-stock 쿼리 방지).

### 6.2 `scripts/close_screener_performance.py`

```
옵션: --report-dir data/reports, --input (단일 CSV 지정), --min-days-ago (기본 1)
출력: data/reports/close_performance_<ts>.json (배치별+전체 통계)
      data/reports/close_performance_<ts>.csv  (종목별 상세)
```

- 발굴일은 CSV의 `signal_date` 컬럼 우선, 없으면 파일명 타임스탬프에서 파싱.
- 통계: 배치별/전체 승률(open/close 각각), 평균·중앙값·최고·최저 수익률.

## 7. 승률 검증 계획

1. 매일 스크리너 실행으로 후보 기록 축적 (CSV 파일 = 발굴 이력).
2. `--min-days-ago 1` 이후 성과 스크립트로 익일 매도 수익률 검증.
3. 판정 기준(초안): **시가 매도 기준 승률 ≥ 50% 且 평균 수익률 > 0%** 이면 전략 유효 후보.
   60거래일 이상 표본 축적 후 기준 재설정 (표본 < 30이면 참고용으로만 해석).
4. 시가 매도 vs 종가 매도 중 승률/기대값이 높은 쪽을 실매도 규칙으로 채택.

## 8. 테스트 계획 (DB 없이 호스트 실행)

- `tests/test_close_screener.py`: 모의 객체(mock cursor)/합성 DataFrame 사용.
- 검증 항목: 지표 계산 정확성·경계(high==low, 이력 부족 제외), 점수 상한/하한·단조성,
  하드 필터, 랭킹/top-n, reason 포맷, CSV 출력 포맷(컬럼 순서·반올림),
  날짜 처리(mock cursor로 resolve_trade_date), 파일명 타임스탬프 파싱,
  성과 통계(승률/평균/중앙값) 계산.
- 실행: `python3.11 -m pytest tests/test_close_screener.py -q` (호스트, DB 불필요).

## 9. 리스크 및 한계

| 리스크 | 내용 | 완화 |
|---|---|---|
| 체결 불가능성 | 종가 시그널은 종가 확정 후 산출 → 실제 체결가는 종가~익일 시가 사이 | performance에서 시가 매도(보수적) 병행 검증 |
| 수급 데이터 공백 | 종목별 외국인/기관(`foreign_institutional`) 0행 | 공매도+시장 레짐으로 축소 반영; 수집 파이프라인 복구 시 종목별 점수 확장 |
| 공매도 데이터 지연 | KRX 공매도 집계 T+1~2 지연, 최근 3일 placeholder 행 | 5거래일 평균 사용 + 숫자 아닌 코드 필터링 |
| 갭 리스크 | 익일 시가 갭다운 시 손실 확대 | 유동성 필터, 종목 분산, 레짐 음수일 감점 |
| 표본 편향 | 상장폐지/거래정지 종목 누락 가능(생존편향), 특정 시장 국면 편향 | 장기간 축적 후 판정, §7 기준 주기적 재검토 |
| 임계값 임의성 | 거래대금 3억/최저가 1,000원 등은 합리적 기본값 | CLI로 조정 가능, 운영 데이터로 보정 |
