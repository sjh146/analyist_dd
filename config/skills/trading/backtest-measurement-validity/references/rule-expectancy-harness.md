# 룰 단독 기대값 하네스 — 구현 레시피

목적: 신호를 모델에 넣기 전에 **그 신호 하나만으로 매매했을 때의 기대값**을 재고, 동시에
"아무 규칙도 없는 기준선"을 같은 실행에서 낸다. 판정은 초과수익(= 규칙 − 기준선)으로 한다
(원칙은 `backtest-measurement-validity` §4e).

## 1. 데이터 적재 (실측 컬럼 기준)

```sql
-- 가격: 시장·상품군 필터를 조인으로 건다(유니버스가 바뀌면 결론이 뒤집힌다)
SELECT m.stock_code AS code, m.trade_date AS dt, m.close_price AS close,
       m.volume, m.trading_value AS value
FROM market_data m JOIN stocks s ON s.stock_code = m.stock_code
WHERE m.trade_date BETWEEN %s AND %s AND m.close_price > 0
  AND s.instrument_type = 'STOCK'   -- STOCK|ETF|ETN
  AND s.market = 'KOSPI';           -- 선택

-- 지수(레짐 분할용): index_daily(index_code, trade_date, close)
SELECT trade_date AS dt, close FROM index_daily
WHERE index_code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date;
```

- 테이블 컬럼명은 추측하지 말고 `information_schema.columns` 로 확인한다. 공매도 테이블은
  `volume` 이 아니라 `total_volume`/`short_ratio` 였고, 잘못된 컬럼 참조는 **n=0** 으로 조용히 비었다.

## 2. 파생값

- `fwd1 = close.shift(-1)/close - 1` — **종목별 groupby** 로 시프트(전체 시프트는 종목 경계를 넘는다).
- `r_day = close/close.shift(1) - 1`, `value_ma5 = value.shift(1).rolling(5, min_periods=3).mean()`
  (shift 필수 — 당일 값을 이동평균에 넣으면 look-ahead).
- 지수 병합 후 `idx_ret` 기준으로 `panic = idx_ret <= -0.015`, `normal = 그 외`.
- 비용: `cost = (cost_bps + 2*slip_bps)/10000` (기본 21 + 40 = **61bp**). 기대값은 항상 `fwd1 - cost`.

## 3. 규칙 세트 (대칭으로 구성한다)

생산 규칙만 재면 뒤집힌 엣지를 놓친다 — 항상 **미러 쌍**을 함께 넣는다.

| 계열 | 규칙 |
|---|---|
| 추격 | 당일 +3~8%, +10%↑, 거래대금 5일평균×3 + 상승 |
| 역추세(미러) | 당일 −3~−8%, −8%↓(**핵심**), −20%↓ |
| 수급 | 공매도 비중 상위/하위 10%(일별 분위) |
| 뉴스 | mention_7d 상위 10%(>0 조건) |

분위는 **날짜별** `transform(lambda s: s.quantile(q))` 로 잡는다(전체 분위는 시장 드리프트를 섞는다).

## 4. 출력 계약 (JSON)

```json
{ "window": ["2026-01-02", "2026-11-19"], "cost_pct": 0.61,
  "universe": {"market": null, "instrument": "STOCK"},
  "regime_index": "KOSDAQ",
  "baseline_all": {"n": 0, "mean_net_pct": 0, "win_rate": 0, "std_pct": 0, "t": 0,
                   "panic": {}, "normal": {}},
  "rules": { "R8_down_8plus": {"n": 0, "mean_net_pct": 0, "excess_pct": 0,
                                 "win_rate": 0, "median_net_pct": 0, "t": 0,
                                 "panic": {}, "normal": {}} } }
```

- `excess_pct` 는 코드가 계산해 저장한다(나중에 손으로 빼지 않는다).
- `median_net_pct` 를 **반드시** 함께 낸다: 평균 (+) & 중앙값 (−) 는 소수 대박이 만든 평균이다.
- 표준편차를 저장하지 않았다면 t 를 근사해 쓰지 말고 `null` 로 두어라(근사 t 로 결론내면 철회하게 된다).

## 5. 구현 함정

- **조건식에 `&` 가 들어간 파이썬을 `python3 - <<EOF` 로 인라인 실행하면 셸이 백그라운드 연산자로
  해석해 실행이 거부된다.** 판다스 불리언 마스크는 거의 항상 `&` 를 쓰므로, 프로브는 **임시 .py 파일로
  써서 실행**한다(`scripts/<name>.py` 또는 `tmp/<name>.py`).
- 콘솔 출력은 `rule / n / mean_net% / excess% / win% / t` 순서로 고정하고, 패닉/정상 분해는
  규칙 바로 아랫줄(`└`)에 찍어 눈으로 대조되게 한다.
- 자기신고 1줄: `[claim] rule_expectancy -> <json> rules=N n_total=M`.
- 같은 입력·같은 창이면 재실행 수치가 같아야 한다(분위 계산이 결정적인지 확인).

## 6. 결과를 읽는 순서

1. 기준선이 얼마인가 (음수면 시장 드리프트가 지배하는 구간이다 — 그 자체가 정보다).
2. 규칙 초과수익의 **부호**가 기준선 대비 바뀌는가.
3. t 값 (거래당 sd 가 크면 +0.5%/건도 노이즈).
4. 반기/월별 부호 (한 구간이라도 유의하게 반대면 "불안정").
5. 미러 규칙이 (+)인가 → 그 규칙만 체결 가능성(하한가 잠김 비율)을 따로 잰다.
6. 유니버스를 바꿔도 방향이 유지되는가(시장·상품군 재실행).
