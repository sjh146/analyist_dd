# 단타 스크리너 파이프라인 지연 단축 (측정→적용→비트 동일 검증)

실측 2건: `scripts/daytrading_screener.py` 8.17s → **2.65s**, `scripts/close_screener.py` 500s+ → **2.89s**
(둘 다 후보 20건의 코드·점수·순서 완전 동일). 장중 재발행 체인(브리지 랭킹 조회 2회 + 스크리너 + 발행) 약 3.6s.

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

## 4) 종목별 파이썬 루프 내부도 벡터화한다

`for code, grp in df.groupby(...)` 는 남겨도 되지만 그 안에서 `grp.iloc[-1]["col"]`·`g["col"]` 로 스칼라를
꺼내면 종목당 수십 번의 Series 생성이 붙는다. 루프 앞에서 한 번 `df.sort_values(["stock_code","trade_date"])`
하고 그룹별로 `grp[[...]].to_numpy(dtype="float64")` 한 번을 뜬 뒤 인덱스 슬라이스(`arr[-6:]`, `arr[-6:-1,1].mean()`)로
쓰면 같은 결과에 오버헤드가 사라진다(500s → 2.89s 사례의 두 번째 몫).

## 5) 옛 경로가 너무 느려 기준선을 못 만들 때의 등가성 증명

전체 실행이 타임아웃 나는 최적화는 "전/후 CSV 동일" 을 만들 수 없다. 그때는 **쿼리 수준 등가성**을 먼저 증명한다:

```python
codes = <최신 거래일 상위 200 종목>
old = pd.read_sql(<ROW_NUMBER 질의 + stock_code = ANY(%s)>, conn, params=(codes,))
new = pd.read_sql(<LATERAL 질의 + unnest(%s::text[])>, conn, params=(codes,))
print(len(old), len(new), old.reset_index(drop=True).equals(new.reset_index(drop=True)))
```

실측 1,400행 완전 일치 → 그 다음 새 경로로 산출한 후보 20건의 코드·점수·상위 순서를 비교하고, 회귀 테스트
(`--date` 고정 + 판정 함수 단위 테스트)를 붙인다. 옛 질의는 표본에 `stock_code = ANY(codes)` 를 추가해 제한한다
(안 하면 등가성 검사 자체가 분 단위로 느려진다).

## 함정

- `_feature_value(row, ...)` 처럼 `row.index` 를 쓰는 헬퍼에 `dict` 를 넘기면 AttributeError 로 배치 경로가
  통째로 폴백(느린 길)된다 — 특징행렬 구축은 `df.iterrows()` 의 Series 로 돌린다.
- 내부 정렬용 파생 컬럼(`_rank_score` 등)을 만들면 **출력 직전에 drop** 한다. 안 지우면 산출 JSON 에 섞여
  소비자/테스트가 새 키를 본다.
- 가점을 `score` 자체에 더하면 트레이더의 R1 문턱 의미가 바뀐다 → 정렬 전용 컬럼으로만 쓴다(공개 score 불변).
- **위임받은 에이전트의 실행시간 보고를 그대로 쓰지 마라**: 내가 먼저 잰 값(500s+)과 보고값(2.66s)이 100배 이상
  벌어지면 측정 대상이 다르거나 측정 전후 코드가 다르다. 같은 명령을 내가 `time` 으로 재실행하고, 등가성 증거
  (쿼리 비교·CSV 비교·테스트 수)까지 함께 확인한 뒤에만 채택한다.
