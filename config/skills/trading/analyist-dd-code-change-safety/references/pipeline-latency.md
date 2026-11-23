# 단타 스크리너 파이프라인 지연 단축 (측정→적용→비트 동일 검증)

실측: `scripts/daytrading_screener.py` 전체 8.17s → **2.65s** (후보 20건의 코드·점수 완전 동일).
장중 재발행 체인(브리지 랭킹 조회 2회 + 스크리너 + 발행) 약 3.6s.

## 측정 절차 (최적화 전에 반드시)

1. 베이스라인 산출물을 남긴다: `time python3 scripts/daytrading_screener.py --top-n 20 --json-out /tmp/base.json`
   와 로그의 단계별 타임스탬프(유니버스 → 룩백 → 하드 필터) — 어느 단계가 지배적인지 먼저 본다.
2. 한 번에 한 변경만 적용하고 `--json-out` 을 다시 떠서 **코드·점수 비트 비교**:
   ```python
   a=[(i['stock_code'], round(i['score'],2)) for i in json.load(open('/tmp/base.json'))['items']]
   b=[(i['stock_code'], round(i['score'],2)) for i in json.load(open('/tmp/opt.json'))['items']]
   assert a == b
   ```
3. 성능 최적화는 **결과가 동일하지 않으면 리팩터링이 아니라 로직 변경**이다 — 동일성을 못 보이면 되돌린다.

## 1) 종목별 top-N 조회: 윈도우 정렬 → `CROSS JOIN LATERAL … LIMIT n`

`ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY trade_date DESC)` 로 전체 322만 행을 정렬하면
3.68s. 종목별 인덱스(`(stock_code, trade_date)` UNIQUE)를 타는 lateral 이 0.31s.

```sql
SELECT m.stock_code, m.trade_date, m.open_price::float8, m.close_price::float8, ...
FROM unnest(%s::text[]) AS c(code)                 -- 유니버스 코드만 (생략하면 시장 전체)
CROSS JOIN LATERAL (
    SELECT * FROM market_data md
    WHERE md.stock_code = c.code AND md.trade_date <= %s
    ORDER BY md.trade_date DESC
    LIMIT %s                                        -- lookback
) m
ORDER BY m.stock_code, m.trade_date
```

- 호출부에서 **유니버스 코드를 넘긴다**(`provider.load_lookback(..., codes=sorted(universe_codes))`).
  전체 시장(3,974 코드) 대신 1,858 코드면 조회가 절반으로 줄어 1.27s → 0.37s.
- `codes=None` 폴백을 유지해 기존 호출·테스트가 깨지지 않게 하고, `FixtureProvider` 같은 테스트 더블의
  시그니처도 같이 넓힌다(안 넓히면 `unexpected keyword argument 'codes'` 로 파이프라인 테스트가 전부 깨진다).

## 2) 비싼 종목별 계산 전에 싼 사전 필터를 둔다

칼만 평활화는 종목당 객체를 만들어야 해서 1,858 종목이면 2.4s. 유동성·가격·거래량 조건은 **종목별 마지막 행**만
있으면 판정되므로 먼저 걸러 대상을 669 종목으로 줄이고(0.9s), 계약상 `filter_candidates`(칼만 피처 필요)는
그대로 뒤에 유지한다(이중 필터는 멱등 — 결과 불변).

## 3) 모델 예측은 배치 1회로

종목마다 `ensemble.predict(x.reshape(1,-1))` 를 부르면 1,858회 호출이다. 특징행렬을 한 번 만들어
`predict(X)` 한 번으로 줄이되, **행 순서·인덱스가 어긋나면 Series 매핑이 조용히 틀어지므로** 길이 검사를 한 뒤
불일치·예외 시 기존 행별 루프로 폴백한다(동작 보존이 우선).

## 함정

- `_feature_value(row, ...)` 처럼 `row.index` 를 쓰는 헬퍼에 `dict` 를 넘기면 AttributeError 로 배치 경로가
  통째로 폴백(느린 길)된다 — 특징행렬 구축은 `df.iterrows()` 의 Series 로 돌린다.
- 내부 정렬용 파생 컬럼(`_rank_score` 등)을 만들면 **출력 직전에 drop** 한다. 안 지우면 산출 JSON 에 섞여
  소비자/테스트가 새 키를 본다.
- 가점을 `score` 자체에 더하면 트레이더의 R1 문턱 의미가 바뀐다 → 정렬 전용 컬럼으로만 쓴다(공개 score 불변).
