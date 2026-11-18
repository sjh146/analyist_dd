# 스펙 — 뉴스/SNS 종목 연결 커버리지 수리 (죽은 피처 부활 2단계)

작성 2026-11-19. 근거: 패널 재빌드(v2) 후에도 **실질 부활 0개** — 원인은 소스 공백이 아니라
**종목 연결(coverage/linkage)** 이었다. 실측(2026-11-19 00:30, 패널 v2 200종목 기준):

| 소스 | 패널 200종목 중 커버 | 행 수 |
|---|---|---|
| `event_features` | **192/200** | 15,458 |
| `foreign_institutional` | 94/200 | 15,092 |
| `news_events` | **31/200** | 192 |
| `sns_post_features` | **31/200** | 1,861 |
| `stock_sentiment` | **11/200** | 15 |

그리고 `news_events` 전체 12,828행 중 **6,854행(53.4%)이 `stock_code IS NULL`**.

## 원인 (코드 확인 완료)

`services/news-analyzer/app/main.py::_save_structured_event`:
```python
stock_code = None
candidate = (structured.stock_code or "").strip()   # 파서가 뽑은 '종목명 후보'
match = self.pg_storage.get_stock_by_name(candidate) # 정확히 일치하는 이름만
if match and match.get("stock_code"): stock_code = match["stock_code"]
# 실패 → None 으로 저장(관계 무단 생성 방지)
```
→ **종목명 표기 차이**("삼성전자" vs "삼성전자(주)", "SK하이닉스" vs "SK 하이닉스", 약칭·별칭)가
매칭 실패로 이어지고, 실패는 **조용히 NULL** 로 남아 어떤 이름이 실패했는지 관측할 수 없다.

## 1. 매칭 정규화 + 실패 관측 (핵심 변경)

`services/news-analyzer/app/storage/postgres_storage.py` 의 `get_stock_by_name`:

1. **정규화 매칭 순서**(앞에서 성공하면 종료):
   ① 원문 그대로 ② 공백·`(주)`·`㈜`·`주식회사`·`(유)`·대소문자 제거 후 equality
   ③ 위 정규화 키로 **유니크할 때만** 부분일치(2건 이상이면 실패 처리 — 오매핑 금지)
   ④ 별칭 테이블 `stock_name_aliases(alias_norm PK, stock_code, created_at)` 조회
2. **실패 이름 기록**: 매칭 실패 시 `news_stock_match_failures(name_raw, name_norm, hits, created_at)`
   에 upsert(횟수 증가). 3회 이상 실패한 이름은 로그 WARNING 1회(중복 억제).
3. 기존 계약 불변: **매칭 실패 시 이벤트는 저장하되 `stock_code=None`** (관계 무단 생성 금지).

## 2. 과거 NULL 재처리 스크립트 `scripts/news_stock_link_backfill.py`

```
python3 scripts/news_stock_link_backfill.py [--days 120] [--limit 0] [--dry-run]
```
- 대상: `news_events.stock_code IS NULL` 행 + 대응 `news_event_extraction`(원문 이름 후보 보관 테이블)
- 새 매칭 함수로 재시도 → 성공한 행만 `UPDATE news_events SET stock_code=...`(그 외 컬럼 무변경)
- 자기신고: `{scanned, matched, still_null, by_rule:{exact,norm,partial,alias}}` + `--dry-run` 지원
- **멱등**: 같은 실행을 두 번 해도 matched 0(이미 채워짐). 실패 행은 건드리지 않는다.
- DB 쓰기는 이 두 테이블(`news_events`, `news_stock_match_failures`, `stock_name_aliases`)만.

## 3. 테스트 — `tests/test_news_stock_link.py` (신규 6개)

① 정규화 키("삼성전자(주)" → "삼성전자") ② 부분일치 유니크 성공 ③ 부분일치 2건 이상이면 실패
④ 별칭 테이블 경유 매칭 ⑤ 실패 이름 upsert 멱등(2회 → 1행, hits=2) ⑥ 재처리 스크립트 멱등
(이미 stock_code 있는 행은 건드리지 않음)

## 4. 검증 (실행 출력 원문 첨부)

```
docker exec stock_news_analyzer python -m pytest tests/test_news_stock_link.py -q   # 컨테이너에 pytest 있으면
/usr/bin/python3 -m pytest tests/test_news_stock_link.py -q                        # 아니면 호스트
/usr/bin/python3 scripts/news_stock_link_backfill.py --days 120 --dry-run
# 실측 재확인(쓰기 후)
psql: select count(*) filter (where stock_code is null), count(*) from news_events;
```
기대: NULL 비율 53.4% → 30% 이하, `news_events` 패널 200종목 커버 31 → 80 이상.

## 5. 금지

- 버전관리 금지. 실주문·브리지·트레이더 코드 무접근. 기존 테스트 기대값 수정 금지.
- `news_events` 의 `stock_code` 외 컬럼 수정 금지. 다른 테이블 쓰기 금지(신규 2개 + 위 1개만).
- 추측 매칭 금지: 부분일치가 2건 이상이면 **실패로 남긴다**(잘못된 종목에 뉴스를 붙이면 그게 누수·오염이다).
