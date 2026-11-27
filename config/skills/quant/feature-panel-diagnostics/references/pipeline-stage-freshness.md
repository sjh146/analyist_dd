# 수집 → 정제 → 학습 → 추론 전 구간 신선도 점검

"뉴스·SNS·정제·학습·추론이 원활한가" 류의 질문에 **단계별 최신일과 건수**로 답하는 체크리스트.
한 단계라도 하루 밀리면 그 아래 전부가 낡은 데이터로 돌아가므로, 항상 위에서 아래로 읽는다.

## 1. 단계별 한 줄 쿼리 (호스트, `POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434`)

```sql
select count(*), max(event_date)              from news_events;        -- ① 뉴스 원천
select count(*), max(posted_at::date),
       count(distinct stock_code)              from sns_posts;          -- ② SNS 원천
select count(*), max(trade_date),
       count(distinct stock_code)              from sns_post_features;  -- ③ SNS 피처(정제)
select count(*), max(trade_date)              from event_features;     -- ④ 이벤트 피처(정제)
select count(*), max(rcept_dt)                from disclosures;        -- ⑤ 공시 원천
select prediction_date, count(*), max(created_at) from ml_predictions
 group by 1 order by 1 desc limit 3;                                   -- ⑥ 추론 저장
select max(computed_at), count(*)             from feature_coverage;   -- ⑦ 커버리지 스냅샷
```

판정 규칙:

- ①~⑤ 의 최신일이 **마지막 완료 거래일**과 같아야 한다. 하루 어긋나면 아래 단계를 읽기 전에 §5b(순서·시계)를 본다.
- ⑥ 은 설계상 **전일 완료 세션**을 가리킨다(오늘 장 마감 후 예측은 다음 새벽에 생성). "오늘 날짜가 없다"만으로
  누락이라 쓰지 말고 컨테이너 `date` 와 `created_at` 을 함께 본다.
- ⑦ 의 `computed_at` 이 여러 값으로 갈리면 일부 행만 갱신된 것이다(빌더별 조각 갱신).

## 2. 학습이 실제로 결정에 닿는지 (배선 확인)

```bash
# 피드를 발행한 파이프라인 로그에 모델 예측 줄이 있는가
grep -E '모델 예측|model_prob' reports/cron_daytrading_pipeline.log | tail -3
# 발행물에 확률이 실렸는가 + 분포 폭
python3 -c "import json,statistics as st; f=json.load(open('data/feed/screener_latest.json'));
 it=(f.get('candidates',{}).get('daytrading',{}) or {}).get('items') or [];
 p=[x['model_prob'] for x in it if x.get('model_prob') is not None];
 print(len(it), len(p), min(p), st.median(p), max(p))"
```

- 확률이 실려 있어도 **스프레드가 0.05 이하**면 순위 변별력이 없다는 뜻이다(실측 0.347~0.397). 그 상태에서
  "추론이 작동한다"와 "모델이 순위를 정한다"는 다른 문장이다 — 피드 `score` 상위 5개와 `model_prob` 상위 5개의
  교집합을 세어(실측 1/5) 보고에 같이 쓴다.
- 반대로 확률 필드가 아예 없으면 파이프라인의 `predictor` 가 `None` 이거나 경로가 빠진 것이다 —
  커버리지 문제가 아니라 배선 누락이다.

## 3. 정제 품질 지표

- 뉴스 수집 로그의 마지막 요약줄에서 `질의 / 기사 / 가드 통과 / 실제 삽입 / rejected` 를 함께 읽는다.
  rejected 비율이 30~40%대면 정상 범위일 수 있으니 **사유별 집계가 없으면 비율만으로 결함이라 쓰지 않는다**.
- `HTTP 429 Too Many Requests` 가 섞여 있으면 재시도로 커버됐는지 마지막 `완료 ... 실패 창 0` 줄로 확인한다.
- 이벤트 유형 분포(`select event_type, count(*) from news_events group by 1`)를 보고, 분류가 한쪽(기타)으로
  쏠려 있으면 그게 정제 품질 문제인지 원천 구성인지 구분해 쓴다.

## 4. 보고 형태

단계별로 `✓ <단계> — 최신일 · 건수 · 종목수` 한 줄씩, 그 다음 **끊긴 단계 하나**와 그 원인·조치, 마지막에
"파이프라인은 흐른다 / 모델 기여는 미미하다"처럼 **연속성과 성능을 분리**한 문장. 단계별 숫자 없이
"정상"이라고만 쓰지 않는다.
