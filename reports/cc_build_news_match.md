구현 완료. 보고:

## 작업 보고 — `get_stock_by_name` 종목명 매칭 정규화

### 변경 파일 (2개만)

**1. `services/news-analyzer/app/storage/postgres_storage.py`** (수정)
- 파일 상단 docstring: WHY + 실측 근거와 조회 명령 원문 추가 (규칙 ①)
- `normalize_stock_name()` 모듈 함수 신설: 공백·`(주)`·`㈜`·`주식회사` 제거 + 대문자 (스펙에 나열된 항목만 — `docs/spec_news_coverage_fix.md`에는 `(유)`도 있으나 작업 지시 목록을 따름, 1줄 추가로 확장 가능)
- `get_stock_by_name` 전면 개편: ①원문 정확일치 → ②정규화 equality → ③별칭 테이블 → ④정규화 부분일치(정확히 1건만 성공, 2건 이상 = 실패). 실패 시 None (기존 계약 유지 — `main.py`가 `stock_code=None`으로 이벤트 저장, 관계 무단 생성 방지)
- `_ensure_stock_name_tables()`: `stock_name_aliases` 생성(멱등 IF NOT EXISTS). `news_stock_match_failures`는 이미 DB에 존재(0행) — CREATE IF NOT EXISTS는 no-op
- `_record_match_failure()`: `ON CONFLICT (name_raw)` upsert로 `hits += 1` (스펙 §3-⑤ "2회 → 1행, hits=2"), 3회 도달 교차 시점에 WARNING 1회(스펙 §1-2)
- 설계 근거: `\d stocks` 실측상 `stock_name`에 인덱스가 없어 어떤 매칭 SQL도 시퀀스 스캔 → 4,352행을 1회 조회해 Python에서 ①~④ 수행(3회 스캔→1회, 정규화 키 단일 정의로 SQL/Python 불일치 원천 제거)

**2. `services/news-analyzer/tests/test_news_stock_match.py`** (신규, 요구된 4개)

### 추측 금지 — 모든 기대값은 실측 (조회 명령은 파일 docstring에 기록)

| 근거 | 실측값 |
|---|---|
| `stocks` 행/이름 distinct | 4,352 / 4,352 (중복 0) |
| `(주)`·`㈜`·`주식회사` 포함 이름 | 0행 (suffix 제거는 입력 쪽에서만 의미) |
| 정규화 키 충돌 (`GROUP BY norm HAVING count>1`) | 0행 (equality 히트는 항상 ≤1) |
| 삼성전자/SK하이닉스/NAVER | 005930 / 000660 / 035420 |
| `하이트진로2` 정규화 포함 | 1건 (000087 하이트진로2우B) — 유니크 부분일치 케이스 |
| `채권혼합` 정확·equality 0, 부분일치 | 41건 — 2건 이상 실패 케이스 |

### 검증 실행 출력 (컨테이너, 스펙 문서가 지정한 실행 경로)

pytest는 컨테이너에 없어 `pip install pytest` (일시적, 컨테이너 재시작 시 소멸) 후 실행:
```
platform linux -- Python 3.11.16, pytest-9.2.0 -- /usr/local/bin/python
collecting ... collected 4 items
tests/test_news_stock_match.py::test_normalized_name_match PASSED        [ 25%]
tests/test_news_stock_match.py::test_unique_partial_match PASSED         [ 50%]
tests/test_news_stock_match.py::test_ambiguous_partial_fails PASSED      [ 75%]
tests/test_news_stock_match.py::test_failure_record_idempotent PASSED    [100%]
============================== 4 passed in 2.27s ===============================
```
- 재실행(멱등 확인): 2회 연속 `4 passed` — 원장에 잔여 행 0건(`SELECT count(*)` 확인)
- 별칭 경로(테스트 4개에 없음)는 1회성 실측으로 검증: `가짜별칭 ZZQ9F3A` → alias 히트 → `{'stock_code': '005930', 'stock_name': '삼성전자'}` 반환, 검증 후 원상복구
- 나머지 news-analyzer 스위트: 111 passed, 1 failed — `test_news_graph_writer.py::TestNoDriver::test_no_driver_returns_zero`는 **Neo4j 테스트로 postgres_storage를 참조하지 않아 기존 실패**(변경 무관, "기존 테스트 수정 금지"에 따라 미수정)

### 자기신고 (규칙 ④)

`dq_claim.py`의 `record_claim`은 **미사용** — 이 함수는 기사당 1회 호출되는 조회 경로로, 수집기(소스 수신/파서 생성/실제 저장 3분리)가 아니다. 호출마다 claim을 남기면 `dq_runner_claim`을 기사 단위로 오염시킨다. 이번 변경의 관측 수단은 스펙이 지정한 **실패 원장(`news_stock_match_failures`) 자체**이며, 테스트가 그 계약(1행 유지 + hits 증가)을 검증한다.

### 금지사항 준수
- git 명령 미실행, 다른 파일 수정 없음, 리포 밖 파일 생성 없음 (pytest 설치는 컨테이너 런타임 환경)
