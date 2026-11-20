구현 완료. 보고입니다.

## 구현 보고 — news_stock_pilot (죽은 피처 2단계 ②)

### 생성한 파일 (신규 3개 — 기존 파일 수정 0건)

| 파일 | 내용 |
|---|---|
| `scripts/news_stock_collect.py` | Google News RSS 종목별 언급 수집 (stdlib ElementTree, 정밀도 가드, 멱등 ON CONFLICT, 커서, dq_claim 3분리) |
| `scripts/news_mention_features.py` | 언급 원장 → (종목, 거래일) 격자 `mention_7d/30d/90d` as-of 피처 (당일 제외, 멱등 upsert) |
| `tests/test_news_stock_collect.py` | 스펙 §3의 **정확히 5개** 테스트 (DB·네트워크 없음) |

테이블 2개는 스크립트가 첫 실제 실행 때 `CREATE TABLE IF NOT EXISTS`로 생성하며, **지금은 만들지 않았음**(쓰기 금지 준수 — DB에 없음을 확인).

### 실측 근거 (docstring에 조회 명령과 함께 기록)
- **feedparser 없음**: 호스트 `/usr/bin/python3`에서 `ModuleNotFoundError` 실측 → stdlib ElementTree 파싱으로 구현
- **창 상한 100건**: 현대차 14일 창 실호출 1회 → item 100건(=상한 도달) → 7일 반창 재분할 구현 (재분할은 결정적 → 커서 재개 재현)
- **가드 실측**: 같은 100건 표본에서 통과 95 / rejected 5 ('현대자동차그룹…' 제목은 '현대차' 연속 부분문자열이 아님)
- **after:/before: 양 끝 포함**: pubDate min 2026-11-05 08:00 ~ max 2026-11-18 08:00 GMT 실측
- **우선주 9종목**: 패널에 '대상우'·'현대차우'·'CJ제일제당 우' 등 9개 — 기저명이 `stocks.stock_name`에 **존재할 때만** '우' 제거 후 질의·매칭 (조회: '대상우'→'대상' 존재 ✓, '성우'→'성' 부재 → 원명 유지). 뉴스는 우선주를 기저명으로만 쓰므로 이 처리가 없으면 그 9종목이 영구 0행
- **짧은 이름 18종목** (금비·엔젯·YW 등) → 독립 토큰 규칙 `(?<![A-Za-z0-9가-힣])이름(?![A-Za-z0-9가-힣])`
- **published_date = UTC 날짜**: KST 새벽 기사는 하루 앞당겨져 as-of 규칙에 보수적 방향(미래 정보 없음) — docstring에 명시

### 검증 (실제 실행 출력)
```
$ /usr/bin/python3 -m pytest tests/test_news_stock_collect.py -q
.....  [100%]          → 5 passed in 0.06s
$ /usr/bin/python3 -m pytest tests/ --collect-only -q
900/908 tests collected (8 deselected) in 3.43s   ← 전체 수집 깨짐 없음

$ python3 scripts/news_stock_collect.py --codes-file reports/panel_codes.txt \
    --from 2026-08-19 --to 2026-11-18 --limit-codes 1 --max-queries 3 --dry-run
완료: 질의 3 / 본 기사 40 / 가드 통과 39 / 실제 삽입 0 / rejected 1 / 창 3 / 종목 1
exit=0, 커서 파일 없음, DB 테이블 없음 확인

$ python3 scripts/news_mention_features.py --from 2026-08-19 --to 2026-11-18 --dry-run
→ 테이블 없음 경로 0행 처리, exit=0
```
- **SQL 검증(ROLLBACK 트랜잭션 — 영구 기록 없음)**: 두 테이블 DDL 생성 → ON CONFLICT DO NOTHING 2회 삽입 `rowcount 1→0, 행 1개`(멱등) → features upsert 동일 키 갱신 `1행 유지` → `compute_grid` 수동 계산과 일치 → `TRADES_SQL` 실데이터 000120 거래일 62행(2026-08-19~11-18) 확인 → rollback
- **창 산술**: 7개 창이 08-19~11-18을 빈틈 없이 덮음, 분할 결정적, 기본 질의 200×7=1400 < 예산 2000

### 검증 중 잡아 고친 버그 2건
1. dry-run이 커서 파일을 썼음(`save_cursor` 가드 누락) → `not a.dry_run` 가드 추가, 잔존 파일 삭제
2. features 스크립트가 테이블 부재 시 `InFailedSqlTransaction`으로 사망 → UndefinedTable 후 `conn.rollback()`으로 수정

### 규칙 준수
git 미실행 · LLM 미호출 · 기존 파일 수정 0 · 리포 밖 파일 없음(/tmp 임시 샘플은 사용 후 삭제) · 실제 수집 실행 안 함(dry-run까지만 — 본 실행·커버리지 검증은 오케스트레이터 몫). dq_claim은 `claim_start`/`claim_finish`로 재사용 (source=소스 기사 수, claimed=가드 통과 수, persisted=실삽입 수 3분리).

한 가지 결정 사항: 스펙의 성공 기준 "rejected 비율 < 40%"는 가드 정책상 종목명이 희귀한 이름일수록 높아질 수 있고, `--max-queries 2000`은 200종목×7창=1400에 재분할 여유가 있는 값입니다 — 첫 본 실행 후 커버리지(31→120+) 확인이 성공 기준의 실질 판정입니다.
