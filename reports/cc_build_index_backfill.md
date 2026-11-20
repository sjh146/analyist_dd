모든 검증이 완료되었습니다. 최종 보고입니다.

---

# 구현 보고 — `docs/spec_index_backfill.md` 계약 이행

## ① WHY + 실측 근거 (파일 상단 docstring에 기록, 조회 명령 포함)

- `etf_premium_analysis.py`의 `load_index_change`가 `market_data`에서 `stock_code IN ('KOSPI','KOSDAQ','1001','2001')`로 지수를 찾음 → **0행** (실측 조회)
- `macro_indicators`는 금리·환율·물가 9종뿐 (지수 없음) — 실측 조회
- `information_schema`에서 `%index%` 테이블 **0개** → `index_daily` 신규 필요 확인
- Naver API 실측(2026-11-20, net_guard 경유): KOSPI/KOSDAQ 각 459행(20250102~20261120, 오름차순), 헤더행 홑따옴표·데이터행 쌍따옴표, **`ast.literal_eval(원문)`은 선두 `\n `+행간 탭 때문에 IndentationError 실측 → `.strip()` 필요**. 26년치(6,627/6,626행) 결측값 0건 확인 → 결측 스킵은 방어적 처리로 구현.

## ② 변경 파일 (신규 2개만, 기존 파일 수정 없음)

| 파일 | 내용 |
|---|---|
| `scripts/index_backfill.py` (신규) | CLI `--from/--to/--codes/--dry-run`, `CREATE TABLE IF NOT EXISTS index_daily` (스펙 DDL 그대로), `ON CONFLICT DO UPDATE` upsert, `dq_claim.record_claim` 재사용, net_guard('naver') 경유(1.0s+0.2s 지터) |
| `tests/test_index_backfill.py` (신규) | 스펙 테스트 3개: ① 응답 파싱(홑따옴표 헤더·탭) ② upsert 멱등(sqlite in-memory, 2회 실행 행수 불변 + DO UPDATE 갱신 확인) ③ 결측 행 스킵+카운트 |

기존 파일 수정 **없음**. 개발 중 발견해 수정한 자체 버그 1건: `store_rows` 호출부에 `conn` 누락(TypeError로 첫 실행이 store 직전 중단 — 테이블만 생성된 상태였고 재실행으로 복구, 멱등 동작 확인됨).

## ③ 검증 실행 출력

**pytest:**
```
$ /usr/bin/python3 -m pytest tests/test_index_backfill.py -q
...                                                                      [100%]
3 passed in 0.11s
```

**--dry-run** (조회·파싱만, 저장 없음):
```
[backfill] KOSPI 2025-01-01~2026-11-20 원천 459행 → 유효 459행(결측 스킵 0) · upsert 0행
[backfill] KOSDAQ 2025-01-01~2026-11-20 원천 459행 → 유효 459행(결측 스킵 0) · upsert 0행
[dry-run] 저장 없음(조회·파싱만) — rows_parsed=918
```

**실제 백필:**
```
codes=2, window=2025-01-01~2026-11-20, rows_received=918, rows_parsed=918, rows_written=918, skipped=0, codes_empty=0, codes_failed=0
[claim] index_backfill -> index_daily source=918 claimed=918 persisted=918
```

**멱등 재실행** (같은 창):
```
rows_written=0
[claim] index_backfill -> index_daily source=918 claimed=918 persisted=0
```
재실행 후 `SELECT COUNT(*) FROM index_daily` → **918행 불변** ✓

**DB 검증:**
```
index_daily: ('KOSDAQ', 459, 2025-01-02, 2026-11-20)
index_daily: ('KOSPI', 459, 2025-01-02, 2026-11-20)
KOSPI 2026-11-20: close=4869.96, volume=193704 (API 실측값과 일치)
2026-11-20 행수: 2   ← 스펙 기대 '2026-11-20 행 존재' 충족
dq_runner_claim: runner=index_backfill, source=918, claimed=918, persisted=918 기록 확인
```

**금지사항 준수:** 신규 `index_daily`만 생성(`%index%` 테이블은 index_daily 1개), `market_data` 3,235,305행 불변, net_guard 상태에서 'naver' 키 16콜·차단 0, git 미실행, 비밀번호 출력·기록 없음, 리포 밖 파일 생성 없음. `etf_premium_analysis.py` 연결(스펙 §2)은 지시대로 건드리지 않았습니다.

**스펙 대비 유일한 편차:** 스펙의 `ast.literal_eval` 그대로는 실측에서 IndentationError로 실패 → `.strip()` 후 파싱. docstring에 실측 근거와 함께 기록했고 테스트 ①이 이 형식을 고정합니다.
