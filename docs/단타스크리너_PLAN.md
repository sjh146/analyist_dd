# 단타스크리너 (Day-Trading Screener) 계획서

> 작성일: 2026-08-24
> 목적: 고빈도 시장조성(HFT market-making)이 만드는 스프레드 바운스 노이즈를
> 칼만필터로 제거한 뒤, 학습된 챔피언 모델의 예측과 거래량/변동성 지표를 결합하여
> **당일(단타) 매수 후보를 선별·랭킹**하는 스크리너를 만든다.
> 기존 스크리너(`close_screener.py` 종가, `swing_screener.py` 스윙)와 나란히 운영한다.

---

## 1. 유니버스 / 데이터 소스

### 1.1 유니버스

- **대상**: KOSDAQ 전체 — `stocks.market = 'KOSDAQ'` AND `market_data` 20거래일 이상
  (기존 `close_screener.get_kosdaq_stocks` / `swing_screener.get_kosdaq_stocks`와 동일 셀렉트 패턴).
- **기준**: 시그널일(`signal_date`) = 시그널 산출일. 휴장일이면 해당 날짜 이하의
  최대 거래일로 해석(기존 `resolve_trade_date` 패턴 재사용).
- **하드 필터**(후보 제외): 최소 거래대금(기본 3억, `trading_value` NULL 시 `close×volume` 근사 폴백),
  최소 종가(기본 1,000원), `volume > 0`, 최근 20거래일 미달 종목 제외.

### 1.2 데이터 소스

| 소스 | 테이블 | 용도 | 컬럼 |
|---|---|---|---|
| OHLCV 일봉 | `market_data` | 칼만 입력 + 거래량/변동성 지표 | `trade_date, open_price, high_price, low_price, close_price, volume, trading_value` |
| 종목 메타 | `stocks` | 종목명/섹터 | `stock_code, stock_name, sector` |

- **현재**: 일봉(`market_data`) 기반 (아래 §7 한계에서 자세히).
- **향후**: 분봉(KIS)으로 교체 가능하도록 **Provider 인터페이스**로 추상화 (§3).

---

## 2. 칼만필터 설계 (상태 / 관측 / 파라미터)

### 2.1 문제 정의

HFT 시장조성이 매수호가↔매도호가를 오가며 만드는 스프레드 바운스는 관측 가격에
고주파 노이즈를 더한다. 이 노이즈는 순수한 일봉 수익률(`return_1d`)과 단기 모멘텀
지표를 왜곡한다. 칼만필터는 `관측 가격 = 진짜 추세(상태) + 노이즈(관측오차)` 모형으로
상태(진짜 추세)의 최적 추정치를 시계열적으로 생산한다.

### 2.2 상태 / 관측 / 파라미터

- **상태(x)**: 로그가격의 순간 추세(drift) = 로그 수익률의 기댓값.
- **관측(z)**: 관측된 로그 수익률(`diff(log(close))`) — 스프레드 바운스 노이즈 포함.
- **관측 모형**: `z[k] = x[k] + v[k]`, `v ~ N(0, R)`.
- **상태 전이**: `x[k] = x[k-1] + w[k]`, `w ~ N(0, Q)` (랜덤워크 추세).
- **R (관측 노이즈)**: 최근 21일 로그수익률 분산으로 **적응** (`R_adapt = max(σ²_last21, R/10)`).
  농도가 높은 국면에서 노이즈가 크면 필터가 관측을 덜 믿는다.
- **Q (과정 노이즈)**: 추세가 얼마나 빨리 변하는지. 기본 `Q=5e-5` (기존 `KalmanFeatureFilter` 기본 재사용).

### 2.3 확장 (신규 `kalman_smoother.py`)

기존 `feature_engine/kalman_filter.py`의 `KalmanFeatureFilter.smooth_returns`는
**캐주얼 필터(온라인)의 스칼라 피처 4종**만 반환한다. 단타스크리너는 **종목 전체 시계열을
평활화한 뒤 추세·기울기·노이즈 잔차**를 써야 하므로, 확장 모듈을 신규 생성한다
(기존 파일은 **수정 금지** — 참고 import만):

- `KalmanSmoother` (신규, `feature_engine/kalman_smoother.py`):
  - 입력: `close_prices: np.ndarray`
  - 내부: 반복 필터(순방향)로 `x_est, P_est` 계산 → **Rauch–Tung–Striebel 역방향 평활화**로
    전 구간 평활 상태열 `x_smoothed` 계산.
  - 출력(스크리너용 평활화 시계열 피처):
    - `smoothed` : 평활 추세 열 전체
    - `trend` : 최근 평활 상태(추세 부호·강도)
    - `slope` : 평활 상태열의 최근 기울기 (모멘텀의 노이즈 제거판)
    - `noise_resid_std` : `관측 − 평활` 잔차의 표준편차 (스프레드 바운스 노이즈 크기)
    - `volatility_ann` : 연율화 변동성
    - `smoothing_gain` : 최종 칼만 이득
- **O(n) 복잡도** — 스크리너 핫루프(수천 종목)에 안전. 무거운 MCMC/GP 피팅 금지 (§8).

---

## 3. Provider 추상화 (분봉 교체 준비)

### 3.1 인터페이스

모든 데이터 접근은 `Provider` 인터페이스로 추상화한다. 현재 구현체는 `DbDailyProvider`
(일봉, `market_data`), 이후 `KisMinuteProvider`(분봉, KIS API)로 교체 가능.

```python
class MarketDataProvider(Protocol):
    def get_universe(self) -> list[StockInfo]: ...      # (code, name, sector, latest_date)
    def resolve_signal_date(self, date_str) -> str: ...  # 휴장일 → 최근 거래일
    def load_lookback(self, signal_date: str, lookback: int) -> pd.DataFrame:
        # 컬럼: stock_code, trade_date, open_price, high_price, low_price,
        #       close_price, volume, trading_value
```

- 스크리너 코어는 Provider의 **구체 타입을 알지 못한다** (의존성 역전).
- 일봉 구현에서 분봉으로 바꿀 때, `load_lookback`만 분봉 집계(OHLCV)로 바꾸면 된다.
- **테스트는 `FixtureProvider`(메모리 픽스처)로 대체** — DB 없이 동작(§5).

### 3.2 이점

- DB 고정 로직과 점수 산식의 **관심 분리**.
- 스크리너 순수 로직(칼만/점수/랭킹)은 픽스처 Provider로 단위 테스트.
- 새 소스(분봉, 캐시파일) 추가 시 스크리너 본체 무변경.

---

## 4. 점수 산식 (0~100)

### 4.1 구성 요소와 배점

| 항목 | 배점 | 산식 |
|---|---|---|
| 칼만 추세 강도 | 30 | 노이즈 제거된 추세. `(기울기 정규화 + 잔차 저노이즈 가점)` |
| 챔피언 모델 확률 | 30 | `(champion_prob − 0.5) × 2 × 30`, 모델 미가용 시 0 + reason에 표기 |
| 거래량 급증 | 20 | `min(volume_surge / 3.0, 1.0) × 20` |
| 변동성 정합 | 20 | 노이즈가 작고(잔차 작음) 방향성 변동성이 적정한 종목 가점 |

- 최종 `score = clamp(합계, 0, 100)`. `--top-n`(기본 20) 내림차순 랭킹.
- `reason` 문자열에 드라이버를 사람이 읽게 기록.

### 4.2 칼만 추세 강도 상세

- 평활 시계열의 **최근 기울기** `slope`를 종목군 내 표준편차로 **표준화** → `z_slope`.
- `kalman_pts = clamp(z_slope, -1, 1) × 15 + 15` (중립 기준 15점, 상승추세 가점).
- **노이즈 잔차 보너스**: 잔차 std가 낮을수록(신호가 깨끗할수록) `＋(1 − clamp(noise/기준,0,1))×15` 까지.
- 두 항으로 "꾸준히 완만한 상승(저노이즈)"이 급격한 변동(고노이즈)보다 고득점.

### 4.3 챔피언 모델 확률

- `feature_names.json` 순서 계약 준수 (§9). 입력 벡터는 해당 순서로 구성, `nan→0` 처리.
- 확률 `prob` → `model_pts = clamp((prob − 0.5) × 2, 0, 1) × 30`.
- **모델 라이브러리(xgboost/lightgbm/catboost) 미설치 또는 아티팩트 미가용 시**:
  `prob=None` → `model_pts=0`, `reason`에 `"모델미가용"` 표기. 전체는 칼만+거래량+변동성으로 동작.

---

## 5. 테스트 전략

- **DB 전면 배제**: 컨테이너 down 상태에서 픽스처만으로 전부 통과.
- 픽스처:
  1. **합성 신호 + 노이즈** → 칼만이 추세를 복원하는 정확도 검증 (합성 신호에서
     평활 계열과 참 트렌드의 상관 계수 ≥ 임계값, 잔차가 노이즈보다 작음).
  2. **스코어 산식** 단위 테스트 — 구성 요소별 배점·클램프·순위 결정(단조성, 경계).
  3. **Provider 추상화** — `FixtureProvider`로 스크리너 코어가 DB 없이 풀 파이프라인 동작.
- 실행: `python3 -m pytest tests/test_daytrading_screener*.py -q` (호스트에서만).
- 실패 시: 원인 분석 → 신규 파일만 수정 → 재실행 반복(전부 green).

---

## 6. 산출물 및 CLI

### 6.1 신규 파일 (기존 파일 수정 금지)

| 경로 | 역할 |
|---|---|
| `docs/단타스크리너_PLAN.md` | 본 계획 |
| `services/xgboost-ml/app/feature_engine/kalman_smoother.py` | 칼만 평활화 확장 (RTS smoother) |
| `scripts/day_trading_engine/` (package) | Provider 추상화 + 스코어링 코어 (스크리너 순수 로직) |
| `scripts/daytrading_screener.py` | CLI 엔트리 (`close_screener.py` 패턴) |
| `tests/test_daytrading_screener_engine.py` | 칼만/스코어/랭킹/Provider 단위 테스트 |

### 6.2 CLI

```
옵션: --top-n (기본 20), --date YYYY-MM-DD, --output PATH
      --min-trading-value (기본 300000000), --min-price (기본 1000), --lookback (기본 20)
출력: data/reports/daytrading_candidates_<YYYYMMDD_HHMMSS>.csv
컬럼: rank, stock_code, stock_name, sector, signal_date, close_price, score,
      kalman_slope, noise_resid_std, volume_surge, volatility_ann, champion_prob,
      reason
```

- `--help` 정상 동작(완료 기준 2).
- 날짜 미지정 → Provider가 `MAX(trade_date)` 해석; 지정 → 해당 날짜 이하 최대 거래일.
- 가격 이력은 Provider가 종목별 최근 `--lookback` 행을 일괄 로드.

---

## 7. 한계와 대응 (분봉 데이터 부재 시)

### 7.1 핵심 한계

- 현재 **일봉(`market_data`)** 데이터만 DB에 확정된 상태. 진짜 단타(당일 진입/청산)의
  분기점은 **분봉/틱 스프레드 바운스 패턴**이지만, 일봉으로는 장중 노이즈의 원본을 볼 수 없다.

### 7.2 대응 (일봉 기반 단타 신호)

1. **칼만 평활화로 "개장~종가 추세"의 노이즈 제거판을 재구성**:
   일봉 종가열에서 스프레드 바운스에 해당하는 고주파 잔차(`관측−평활`)를 분리 → **저노이즈+상승추세** 종목 선별.
2. **거래대금+거래량 유동성 필터로 단타 가능 종목만** (최소 3억 유동성, 저렴한 스프레드 재료).
3. **챔피언 모델의 다음날 방향 확률**(분봉 대신 일봉 피처로 학습된 모델)을 단기 방향 신호로 사용.
4. **변동성 정합**: 잔차 노이즈가 작고 연율화 변동성이 과도하지 않은 종목에 가점 →
   "노이즈에 휘둘리지 않는 최근 추세"를 포착.

### 7.3 분봉(KIS) 도입 시 확장 경로

- `KisMinuteProvider`가 `load_lookback`을 분봉 OHLCV 집계로 구현 → 스크리너 본체 무변경.
- 칼만 입력을 일봉 종가 → **분봉 종가**로 바꾸면 장중 스프레드 바운스 원본을 직접 평활화
  (스크리너는 Provider가 `1일(240분봉)`을 반환하는 것만 알면 됨).
- 그동안 신호는 **일봉 기반으로 유효하며**, 실행 주기는 장 마감 후 + 다음날 개장 전.

---

## 8. 성능/리소스 제약 준수

- **O(n) 칼만 필터 + RTS 평활화**(n=최근 20~60 관측)만 사용. **MCMC/GP 피팅 금지** (§1.2, GPU 없음, RAM 15GB).
- 픽스처 테스트는 수천 종목 전체 루프 대신 소규모 샘플로 수행.
- 모델 예측은 벡터화(불가 시 종목 단위 단순 예측, 허용)하되, 미가용 시 즉시 폴백.

---

## 9. 챔피언 모델 계약 (feature_names.json)

- 스크리너가 쓰는 피처 순서는 **오직 `services/xgboost-ml/app/models/champion/feature_names.json`** 이 정답
  (swing_screener의 주석과 동일 원칙: pipeline.get_feature_names() 149개는 학습 시점과 불일치).
- 단타스크리너는 이 순서로 벡터를 구성한다. **칼만 피처**는 파일에 이미 있는
  `kalman_momentum_1d`, `kalman_momentum_5d`, `kalman_volatility`(판존)를 사용하고,
  나머지 피처는 피처 파이프라인 재사용 전략은 **swing_screener 경로**를 따른다.
- **모델 파일 보호**: `services/xgboost-ml/app/models/` 하위에 **절대 쓰지 않는다** (§10).
  테스트에서 모델 pkl을 로드하지 않음(호스트에 xgboost/lightgbm/catboost 라이브러리 없음).

---

## 10. 제약 사항 정리

1. **기존 파일 수정 금지** — 신규 파일만 생성. 기존 파일은 참고용 읽기.
2. `services/xgboost-ml/app/models/` 하위 절대 쓰기 금지 (챔피언 pkl 보호).
3. 컨테이너에서 pytest 미실행. **테스트는 호스트에서만** (`python3 -m pytest tests/test_daytrading_screener*.py`).
4. `.env` 읽기 금지 (시크릿).
5. 질문 금지 — 가정은 본 문서에 명시.
6. Git 커밋/푸시 금지 (Hermes 검토 후 처리).
7. 챔피언 모델 입력 순서는 `feature_names.json` 계약 준수(§9).
8. GPU 없음, RAM 15GB — 핫루프에 무거운 피팅 금지, 칼만은 O(n).

---

## 11. 완료 기준

1. `docs/단타스크리너_PLAN.md` 존재 (본 문서.
2. `scripts/daytrading_screener.py --help` 정상 동작.
3. `python3 -m pytest tests/test_daytrading_screener*.py -q` → green.
4. 마지막 메시지: 신규 파일 절대 경로 목록 + 테스트 결과 + 실행 예시 1개.
