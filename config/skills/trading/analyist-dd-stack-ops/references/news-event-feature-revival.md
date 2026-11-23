# 뉴스 이벤트 피처(event_*_5d / theme_exposure_5d) 부활 절차

죽은 뉴스 이벤트 피처를 되살릴 때의 순서와 함정. 실행 스크립트는 저장소
`scripts/news_event_backfill.py`(규칙 기반 추출 → news_event_extraction → 클러스터 → news_events).

## 순서
1. `--stage verify`(stock_xgboost_ml 컨테이너)로 **before** 를 먼저 찍는다. 피처 리더는
   `/app/app/feature_engine/news_event_features.py`. 뉴스 피처는 `now()` 기준 윈도우라
   날짜 인자가 없다 — `(stock, date)` 별로 값이 달라지길 기대하면 안 된다(FeaturePipeline 도 동일).
2. `--stage extract`(stock_news_analyzer): `news_analysis.source='google_news'` 전체를
   제목 우선 종목매칭(`news_revive_pipeline.match_stock` 규칙) 후 EVENT_TAXONOMY 화이트리스트로
   이벤트를 뽑아 INSERT. 중복은 (article_id, event_type) 으로 스킵.
3. `--stage cluster`: `app.storage.PostgresStorage.get_recent_event_extractions` +
   `app.events.clusterer.cluster` + `save_event_cluster` 를 직접 import 해 1회 실행(서비스 재시작 금지).
4. `--stage verify` 로 **after** 를 찍는다. 우주 전체(193종목) 기준 "비영&비상수" 개수를 세면 증거로 충분하다.
5. 되돌리기: `--stage revert` (raw_json->>'mark' = 'rule_backfill_v1' 행만 지운다).

## 함정 (전부 실측)
- **URL 안의 문자열이 키워드로 오탐된다**: Google News RSS 본문은 링크를 포함하고
  `.../rss/articles/CBMi...` 의 `CB` 가 `CB·BW` 로 잡혀 300건 중 192건이 오탐이었다.
  → 태그/URL 제거 + ASCII 키워드는 **대소문자 구분 + 단어 경계**로 매칭한다.
- **한국어 합성어 오탐**: `화재` ⊂ `문화재`(289건), `붕괴` ⊂ `신뢰 붕괴`, `폭발` ⊂ `수요 폭발`.
  → 키워드별 배제 사전(EVENT_EXCLUDE) + 문맥 창 검사. 애매한 키워드는 가중치를 2.0 미만으로
  낮춰 **단독으로는 채택되지 않게** 한다(급등/급락/마감/지수/수급 등).
- **클러스터키 seq 미할당 버그(수정함)**: 클러스터러는 `(stock, type, date, 2h bucket)` 가 같고
  텍스트가 다른 클러스터에 seq 를 올려야 하는데 항상 0을 썼다 → 같은 키로 `ON CONFLICT DO UPDATE`
  되어 **4,680개 클러스터가 563행으로 덮어써졌고** `sum(article_count)` 가 612/5,753 으로 뭉개졌다
  (event_*_5d 가 0/1 이진값이 되는 원인). 검증: `select sum(article_count) from news_events` 가
  `news_event_extraction` 행수와 일치해야 한다.
- **created_at 선택**: 테이블 기본값 `now()`(writer 와 동일)를 쓰면 백필 전체가 오늘 버킷에 들어가
  피처가 즉시 살아난다. 기사 발행시각을 넣으면 과거 이벤트만 살아나고 now-윈도우 피처는 5일 뒤 다시 0이 된다.
  대신 `market_impact_score` 는 7일 베이스라인이 없어 상한(100)에 포화되기 쉽다.
- **테마 3종(theme_count/theme_max_relevance/theme_momentum)은 Neo4j `PART_OF_THEME` 기반**인데
  스택 어디에도 그 관계를 쓰는 코드가 없다(뉴스 그래프 라이터는 `HAS_THEME`). Neo4j 실측: Theme 노드 0,
  PART_OF_THEME 0. 피처 리더를 고치지 않는 한 되살릴 수 없다(별도 작업). 뉴스 쪽 테마 피처는
  `theme_exposure_5d` 하나이며 이것만 되살아난다.
- 검증은 반드시 **리더를 실제 호출**해 세고, 표본이 아니라 news_events 에 있는 종목 전체로 세라
  (표본 12개만 보면 상한 포화 종목만 걸려 market_impact_score 가 상수처럼 보인다).
