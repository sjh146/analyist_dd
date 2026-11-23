# 뉴스/감성 피처 되살리기 — 단계 체인 · 진단 · 커버리지

피처가 상수 0 이라 성능에 기여하지 못하는 상태를 데이터로 되살릴 때의 절차와 진단.
**앞 단계가 안 끝나면 뒤 단계의 숫자는 존재하지 않는다** — 단계를 건너뛰고 결론을 말하지 않는다.

## 단계 체인

1. 원문 수집 → `news_analysis` (`source='google_news'`, Google News RSS, 기간 필터 `after:`/`before:` 동작)
2. 기사별 LLM 감성분석 → `news_analysis.sentiment_score` · `raw_response` (raw_response 안에 `matched_stock_code`)
3. (종목,일) 집계 → **`stock_sentiment`** ← 피처가 실제로 읽는 테이블
4. 피처 비영 확인 (`sentiment_avg`, `sentiment_avg_5d`, `news_count_5d`, `news_count_20d`)
5. 멀티시드 before/after 재학습으로 AUC 델타 측정

구현은 `scripts/news_revive_pipeline.py` (컨테이너 cwd `/app`):
`--stage collect` → `--stage analyze --max-articles <N>` → `--stage aggregate` → `--stage verify`.

## 진단 쿼리 (호스트에서)

```
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc "
SELECT 'news_analysis' t, count(*) c, count(sentiment_score) has_sent, count(raw_response) has_raw FROM news_analysis
UNION ALL SELECT 'stock_sentiment', count(*), count(avg_sentiment), NULL FROM stock_sentiment"
```

- `has_sent`/`has_raw` = 0 이고 `stock_sentiment` 에 최근 며칠·수십 행(라이브 크론 산출)만 있으면
  **그 백필은 피처에 도달하지 않은 것**이다. `news_analysis` 행수만 보고 "백필 완료"로 판정하지 않는다.
- 커버리지(집계가 값을 낼 수 있는 상한): `raw_response::json->>'matched_stock_code'` 로 group by.
  실측 예: 4,885건 분석 → **49종목 / 1,241 (종목,일)** — 6개월 × 50종목 중 20% 수준.
- `analyzed_at` 은 스키마 기본값(`now()`)이 채우므로 분석 여부의 증거가 아니다. `sentiment_score`/`raw_response` 를 본다.
- **분석 백필은 LLM 응답을 통째로 저장한다.** 감성·진위만 골라 저장하면 같은 응답에 있던 `event_type`/`themes`/`importance` 가
  사라져 이벤트 피처(18개)를 되살릴 때 전건 재추출(재과금)이 된다 — `raw_response` 에 파싱 전 원본 JSON 을 그대로 넣는다.
- 검증·피처 호출은 **그 모듈을 가진 컨테이너**에서 한다: `app.feature_engine` 은 xgboost-ml 컨테이너에만 있으므로
  news-analyzer 컨테이너에서 검증 스테이지를 돌리면 `ModuleNotFoundError: No module named 'app.feature_engine'` 로 죽는다
  (리포 `scripts/` 는 여러 컨테이너에 마운트되지만 서비스 `app/` 은 컨테이너마다 다르다).

## 함정

- **analyze 단계는 전건 완료 후 1회 커밋한다.** 결과를 메모리에 모아 `ThreadPoolExecutor` 종료 뒤 `UPDATE` 하므로,
  실행 중에는 DB 가 전부 NULL 로 보인다 — 이걸 "분석 실패"로 오진하지 말고 컨테이너 로그 진행률을 본다:
  `docker exec stock_news_analyzer sh -c 'grep -E "완료|반영" /tmp/analyze.log | tail -3'` (**로그 시각은 UTC, KST-9**).
  전체가 끝나야 커밋되므로 중간에 프로세스를 죽이면 토큰 비용만 쓰고 결과는 0 이다.
- **드라이버가 죽어도 자식은 계속 돈다.** 백그라운드로 띄운 분석 프로세스는 이를 띄운 에이전트가 종료돼도 완주하며,
  집계·검증·리포트는 아무도 하지 않는다 → `pgrep -af 'news_revive|opencode'` 로 드라이버 생존을 먼저 확인하고,
  죽었으면 집계·검증·재학습을 이어서 직접 돌린다.
- **`--universe` 기본값은 `curated`** (KOSDAQ 코드순 50종목). 그 백필로는 173피처 챔피언 경로(전 종목 패널)의
  감성 피처가 살아나지 않는다 → 챔피언 경로를 노리면 `--universe both`(거래대금 상위 200 추가).
- 비용은 기사 수 비례: 실측 4,882건 = 입력 3.4M / 출력 0.83M tok ≈ $1.8 (deepseek-chat 추정 단가). 상한은 `--max-articles`(기본 6000)로 코드 강제.
- 감성 피처가 상수 0 이면 피처 선택에서 **전부 탈락**한다(`reports/ml_result.json` 의 `sentiment_survived: []`).
  "뉴스를 넣었는데 모델이 안 쓴다"의 1차 답은 모델 문제가 아니라 커버리지 0 이다.
- 백필 유니버스가 좁으면 커버리지가 얇아 창 평균(`*_5d`/`*_20d`)이 노이즈가 된다 — 종목당 6개월 100건 수준이면
  기대 개선폭을 크게 잡지 않는다.

## 보고 규칙

before 기준선(예: 뉴스 없는 상태의 멀티시드 평균)을 먼저 명시하고, after 는 같은 시드·같은 유니버스로 측정한 뒤에만 말한다.
측정 전에는 "AUC 가 안 올랐다"가 아니라 **"아직 측정하지 않았다"**로 보고하고, 올랐으면 올랐다고만 보고한다.
