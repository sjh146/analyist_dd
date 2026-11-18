# 스펙 — 종목별 뉴스 언급 수집·집계 파일럿 (죽은 피처 2단계 ②, 비용 0)

작성 2026-11-19. 배경(실측): 패널 200종목 중 뉴스가 붙은 종목은 **31개**뿐이다. RSS 수집기는
**피드 단위**로 받아 기사가 언급한 종목만 잡히고, 종목별 질의가 없다. 그래서 뉴스·감성 피처가
상수 0(또는 nnz 0.02%)이 됐다.

이 파일럿은 **LLM 비용 0**으로 종목별 뉴스를 모아 "언급 건수" 피처를 만든다(감성은 다음 단계).

## 1. 신규 `scripts/news_stock_collect.py`

```
python3 scripts/news_stock_collect.py --codes-file PATH --from 2026-08-19 --to 2026-11-18
        [--window-days 14] [--sleep 1.2] [--max-queries 2000] [--dry-run] [--limit-codes 0]
```
- 소스: Google News RSS `https://news.google.com/rss/search?q={종목명}+after:{from}&before:{to}&hl=ko&gl=KR&ceid=KR:ko`
  (실측: 기간 질의 지원, 창당 최대 100건 → `--window-days 14`로 쪼갠다)
- **정밀도 가드(필수)**: 기사 title 에 종목명이 **그대로 포함**될 때만 저장. 종목명 정규화 길이 <3 이면
  (예: '남성') **제목에 정확히 그 단어가 독립 토큰으로** 있을 때만 통과. 불일치는 `rejected` 로만 집계.
- 저장(멱등): `news_stock_mentions(stock_code text, url text, title text, published_date date,
  source text, matched_by text, created_at timestamptz, PRIMARY KEY (stock_code, url))`
  `INSERT ... ON CONFLICT DO NOTHING`.
- 자기신고: `{queries, articles_seen, inserted, rejected, codes, windows}` + `--dry-run`(쓰기 없음).
- 안전: 호출 간 `--sleep`(기본 1.2s), `--max-queries` 초과 시 정상 종료(다음 실행에서 이어감 —
  진행 커서 `data/state/news_stock_collect_cursor.json`, 인자 바뀌면 커서 무시).

## 2. 신규 `scripts/news_mention_features.py`

```
python3 scripts/news_mention_features.py --from 2026-08-19 --to 2026-11-18 [--dry-run]
```
- `news_stock_mentions` → `(stock_code, trade_date)` 격자 피처:
  `mention_7d`, `mention_30d`, `mention_90d`(해당 거래일 이전 누적, **당일 제외** — as-of)
- 저장: `news_mention_features(stock_code, trade_date, mention_7d int, mention_30d int,
  mention_90d int, PRIMARY KEY (stock_code, trade_date))`, 멱등 upsert.
- 거래일 격자는 `market_data` 의 distinct trade_date(해당 종목) 사용. 뉴스 없는 날은 0(정의상 "언급 없음").

## 3. 테스트 — `tests/test_news_stock_collect.py` (신규 5개)

① RSS XML 파싱(제목·링크·pubDate, 실측 샘플 문자열 사용) ② 정밀도 가드(제목에 종목명 없으면 rejected)
③ 짧은 이름은 독립 토큰만 허용('남성' ⊂ '남성복' 불통과) ④ 멱등(같은 URL 2회 → 1행) ⑤ 커서 저장/재개

## 4. 실행(파일럿) 및 검증

```
/usr/bin/python3 -m pytest tests/test_news_stock_collect.py -q
/usr/bin/python3 scripts/news_stock_collect.py --codes-file reports/panel_codes.txt \
    --from 2026-08-19 --to 2026-11-18 --max-queries 2000
/usr/bin/python3 scripts/news_mention_features.py --from 2026-08-19 --to 2026-11-18
# 확인: 커버리지(패널 200종목 중 언급 1건 이상)와 종목별 건수 분포
```
- `reports/panel_codes.txt` 는 오케스트레이터가 만든다(패널 v2 의 200 코드, 한 줄에 하나).
- 성공 기준: **언급 보유 종목 31 → 120 이상**, 저장 기사 ≥ 2,000건, rejected 비율 < 40%.

## 5. 금지

- 버전관리 금지. LLM 호출 금지(비용 0 유지). 실주문·브리지·트레이더 코드 무접근.
- 기존 테이블 쓰기 금지(신규 2개 테이블만). 기존 스크립트·테스트 수정 금지.
- Google 뉴스 과호출 금지(`--sleep 1.2` 하한), robots/약관 위반 행위 금지.
