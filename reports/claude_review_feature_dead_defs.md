검토 완료. 스키마·계약 게이트·exporter 쿼리·라이브 DB(읽기 전용 SELECT)까지 교차 확인했습니다. 수정은 하지 않았고 보고만 합니다.

---

# 검토 보고: `scripts/feature_dead_definitions_audit.py`

## 질문별 판정

### Q1. A/B 분해 + 판정 기준을 B로 못박는 것 — **대체로 타당 (확신도: 높음)**

**타당한 근거:**
- B(`nonzero_ratio_honest < 0.01`)는 실제 강제 게이트인 FEAT-1의 정의와 정확히 일치한다: `scripts/contract_check.py:85` (`nonzero_ratio_honest < 0.01`), 문턱 45는 `contract_check.py:39` (`MAX_DEAD_FEATURES = 45`). 감사 출력의 주장(`feature_dead_definitions_audit.py:96-97`)은 사실로 검증됨.
- honest는 NaN≠0 규칙을 이미 구현한 지표다: `scripts/feature_coverage_report.py:230`에서 NaN을 분자에서 제외. A의 원천인 `nonzero_ratio`는 NaN을 0으로 채운 뒤 계산(`feature_coverage_report.py:232-234`)하므로, 전량결측 피처를 "살아있음"으로 세는 착시가 A에 내장돼 있다. 판정 기준으로는 B가 우월하다.

**단, 못박기에 두 가지 빠진 것:**
1. **B의 "수"는 인구 혼재 상태로 계산되는데, 그 수를 게이트가 그대로 45와 비교한다.** 오늘 실측: 창 525일 행 24개 중 **18개**가 B-죽음, 창 30일 166행 중 18개, 창 42일 19행 중 5개 — 그리고 **모델 창(120일) 행은 0개**. 즉 오늘의 "죽은 41"은 모델이 실제 쓰는 창의 행을 하나도 포함하지 않은 수다. 계약 여유는 41/45 = 4개뿐인데, 이 4개는 창 라벨 드리프트만으로도 뒤집힌다(레포 실측: 같은 결함이 덮어쓰기 순서로 26↔62 진동, `config/postgres-exporter/queries.yaml:384-395`). 레포는 이미 R38 B안으로 **알림 인구를 모델 창(120일)으로 분리**했는데(`queries.yaml:401-403` `coverage_illusion_count_model_window`), 감사는 이 패턴을 적용해 "120일 창만의 B-죽은 수"를 산출하지 않는다. 정의는 못박았지만, 못박은 정의의 **측정값을 인용 가능한 형태로** 만들지 못했다. (확신도: 높음 — 라이브 DB로 실측 확인)
2. **B 문턱 `< 0.01`은 분모(주식×일수)에 의존한다.** 60종목×30일=1,800행이면 0.01=18행, 60×120=7,200행이면 72행. 같은 피처가 표본·창 크기에 따라 alive/dead를 오간다. docstring이 경고는 한다(`feature_dead_definitions_audit.py:18-22`) — 인지 자체는 반영됨. (확신도: 높음)

### Q2. 인구 혼재 경고가 코드에 충분히 반영됐는가 — **부분 반영, 기계 경로에는 미반영 (확신도: 높음)**

반영됨: docstring `feature_dead_definitions_audit.py:18-22`, stdout 경고 `:98-99`.

미반영 — 세 가지 구멍:
1. **`--json` 산출물에 경고가 전혀 없다** (`:103-106`). `d`에는 개수·창 분포·sparse 목록만 있고 `population_mixed`류 플래그나 주의 문구가 없다. `--json`은 자동화 소비용인데, 기계가 41을 무방비로 인용한다 — docstring이 금지한 바로 그 인용을 JSON 경로가 허용한다. 레포 관례상 JSON에 가드를 넣는 패턴이 이미 있다: `scripts/feature_coverage_report.py:411` (`"stale_guard": ...`). (확신도: 높음)
2. **WIN_SQL이 창별 행수만 세고 창별 죽은 수를 세지 않는다** (`:47`). 오늘 "41 중 18이 525일 창"이라는 사실은 감사 출력만으로는 알 수 없다. 출력된 헤드라인 수(`:88-92`)가 바로 스크립트 자신이 "인용 금지"라고 경고한 혼재 수인데, 인용 가능한 창 분리 수를 만들지 않았다. (확신도: 높음)
3. **표본 크기는 스키마에 없다.** docstring은 "표본 30~60종목" 드리프트를 경고하지만(`:21`), 테이블에 표본 크기 컬럼이 없어 감사가 이 드리프트를 수치로 검출할 수단이 없다(창·측정일만 가능). 경고의 절반은 검출 불가능한 대상이다. (확신도: 높음)

### Q3. NULL→0 오독 함정에 걸리는가 — **함정 구조에 그대로 노출돼 있으나 현재 데이터에서는 미발화 (구조: 높음 / 발화 가능성: 중간)**

SQL 패턴 자체가 그 함정이다. `count(*) FILTER (WHERE nonzero_ratio_honest < 0.01)` (`:37`)은 **NULL 행을 dead에서도 alive에서도 제외**한다 — `NULL < 0.01`은 NULL이기 때문. 즉 NULL은 "죽지 않은 것으로" 조용히 처리된다(스킬 규칙 NaN≠0의 SQL판). 결과:
- `dead_honest + alive_honest ≠ n_rows`가 되고, docstring이 정의한 C = B − A (`:16`)도 깨진다 — `naive=0`인데 `honest IS NULL`인 행은 A에는 들어가고 B에는 안 들어가므로 A ⊄ B. 그런데 스크립트는 합계 불변을 **전혀 검증하지 않는다** (`:35-45`). 이 스크립트의 존재 목적이 "수를 못 맞추는 혼란 해소"인데, 그 혼란의 한 원인(NULL)을 잡는 자리를 비워뒀다.
- 컬럼들이 nullable인 것이 실체적 근거: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS nonzero_ratio_honest DOUBLE PRECISION` — NOT NULL도 DEFAULT도 백필도 없다 (`scripts/feature_coverage_report.py:42-48`, `build_supply_market_features.py:527-531` 등 4개 빌더 동일). 2026-09-25 ALTER 이전 행이 재측정 없이 남거나, 새 빌더 버전이 honest 컬럼을 빠뜨리면 발화한다.
- 라이브 DB 검증(읽기 전용): 오늘은 `honest_null=0`, `null_ratio NULL=0`, 불변 17+24=41 일치 — **현재는 안 터진다**. 다만 계약 게이트(`contract_check.py:85`)가 같은 함정을 공유하므로, NULL이 생기면 게이트가 죽은 수를 **과소집계해 통과**한다 — 감사가 그걸 잡아줄 수 있는 유일한 자리인데 안 잡는다.

---

## 발견 목록 (심각도순)

**F1. 합계 불변 검증 부재 — NULL·레이스 발생 시 조용히 틀린 분해표 출력 (확신도: 높음 — 검증 부재 자체는 사실, 발화는 조건부)**
`feature_dead_definitions_audit.py:35-45`, `:75-81`
`dead_naive+alive_naive == n_rows`, `dead_honest+alive_honest == n_rows`, `dead_naive+sparse_only == dead_honest`(C=B−A) 중 어느 것도 검증하지 않는다. 운영 시나리오: honest 컬럼에 NULL이 하나라도 생기면(위 Q3) 분해표가 자기모순(B≠A+C)인 채 PASS처럼 인쇄된다 — 이 감사의 유일한 산출물이 거짓이 되는 파괴적 실패. 오늘 일치는 우연이다(실측 17+24=41). 처방: `IS NULL` 버킷 1개 추가 + 불변 assert (또는 출력에 불일치 경고).

**F2. 3회 개별 psql 호출 — 스냅샷 불일치 (확신도: 중간)**
`feature_dead_definitions_audit.py:72, 78, 81`
SQL·WIN_SQL·SPARSE_SQL이 별도 커넥션·별도 시점에 실행된다. 그 사이 야간 빌더가 upsert하면 A/C/B가 서로 다른 테이블 상태의 수가 된다. 같은 테이블에서 부분 갱신이 3분 만에 DQ를 뒤집은 실측이 이미 있다 (`config/postgres-exporter/queries.yaml:346-348`, R10 사건). F1의 assert가 있으면 잡히지만 없다. 처방: 한 psql 호출에 합치거나 `BEGIN ... REPEATABLE READ` (단일 docker exec 안에서).

**F3. SPARSE_SQL의 `null_ratio` NULL → `float("")` ValueError 크래시 (확신도: 낮음~중간)**
`feature_dead_definitions_audit.py:49-55` (쿼리), `:79-81` (파싱)
`-tA` 출력에서 NULL은 빈 문자열이다. sparse 행은 `nonzero_ratio_honest < 0.01` 필터로 honest의 비NULL은 보장되지만(`:53`), `null_ratio`는 필터에 없어 NULL이면 `round(NULL::numeric,3)` → 빈 문자열 → `float("")` 크래시, 스택트레이스로 종료. 오늘 실측은 sparse 중 null_ratio NULL 0건이라 미발화. 현재 5개 작성자 전부 null_ratio를 쓰므로 발화 확률은 낮음, 단 방어가 전무.

**F4. (참고) `docker exec -t` TTY 할당 + psql 페이저** — `:59`. 레포의 하우스 패턴(`contract_check.py:48` 동일)이 운영에서 잘 돌고 있어 실질 위험은 낮다고 판단, 보고 대상에서 제외했습니다.

---

## 결론

- 정의 분해(A/B/C)와 B 못박기 자체는 **정당** — 게이트 정의와 일치하고 NaN≠0 규칙을 준수하는 지표를 골랐다.
- 그러나 "정의를 못 박는다"는 목적이 **측정값의 인용 안전성**까지 요구하는데, 그 부분이 비어 있다: 모델 창(120일) 분리 산출 없음, 창별 죽은 수 없음, JSON에 경고 없음, NULL 방어 없음, 합계 불변 검증 없음. 오늘의 헤드라인 "B=41"은 모델 창 행 0개로 계산된 수다.
- 치명도 순: F1(조용한 오판) > F2(시점 불일치) > F3(크래시, 저확률). Q3의 NULL 함정은 현재 데이터에서는 미발화지만 구조적으로 노출돼 있고, 같은 함정을 가진 계약 게이트(`contract_check.py:85`)를 감사가 검증하지 못하는 것이 핵심 공백이다.

수정은 하지 않았습니다(읽기 전용).
