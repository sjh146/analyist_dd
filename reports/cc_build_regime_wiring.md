구현 완료. 최종 보고입니다.

## 구현 보고 — spec_index_backfill.md §2

### ① 변경 파일 (4개 — 이 두 스크립트와 해당 테스트뿐)

**`scripts/etf_premium_analysis.py`**
- `--index-code KOSPI` 옵션 추가. `index_daily`에서 당일·전일 종가를 읽어 전일대비 수익률 `ret = close(d)/close(전일) − 1` 계산
- `build_report`: episode를 **panic(ret ≤ −1.5%)/normal(그 외)** 로 분류 — `is_panic_day`, `panic_threshold_pct` 명시, panic/normal 각각 event study 채움
- 지수 행 없음(당일/전일 결측·조회 실패) → 기존처럼 `null` + 사유 문자열(0 채움 금지), `--index-code` 미지정 시 기존 market_data 프로브 동작 유지
- 기존 시그니처 변경: `load_index_change(conn, date_s, index_code=None)` → `(regime, 사유)` 반환 (호출부는 main뿐)
- 부수 수정: `main()`이 `connect()`를 닫지 않던 누수 수정(스펙 구현에 필요 — 이제 try/finally), `report_to_md`에 by_regime 표 추가, stdout `[claim]` 3분리 자기신고 추가

**`scripts/pairs_arb_research.py`**
- `--use-index KOSPI|KOSDAQ` 옵션: 시장 프록시를 `index_daily` 실지수 종가로 대체(기본은 기존 breadth 유지)
- 신규 `load_index_series(conn, code)` → `{date_str: close}` — `breadth_index`와 같은 모양이라 `regime_map`/`simulate` 결합이 그대로 동작
- `load()`에 source 행수 카운터 추가, `data_note` 동적화, 출력 JSON에 `claim` 블록 내장 + stdout `[claim]`

**테스트** — 기존 기대값 수정 없음, 추가만:
- `tests/test_etf_premium_analysis.py` +3개: ④ index_daily 전일대비 계산(실측값 −5.2148) ⑤ 지수 행 없음 → null+사유 ⑥ panic/normal 분류(경계 −1.5 포함, regime None → null+사유)
- `tests/test_pairs_arb_research.py` +1개: ⑥ load_index_series 실지수 → regime_map down/up 결합
- 두 테스트 모두 in-memory sqlite + `%s`→`?` 치환(`test_index_backfill`의 UPSERT_SQL 관례). 도중 발견한 실측 버그: `sqlite3.Cursor`는 컨텍스트 매니저 미지원(TypeError) → 두 함수를 `cur = conn.cursor()` + try/finally로 수정(주석에 남김)

### ② 규칙 준수
- **WHY·실측 근거**: 두 스크립트 docstring에 실제 조회 명령과 수치 기록 — KOSPI 2026-11-20 close 4869.96 / 전일 5137.89 → **−5.2148%**, index_daily 459행×2종목(2025-01-02~2026-11-20)
- **멱등**: 재실행 → 동일 파일 덮어쓰기, MD5 불변 확인(아래), 실행 시각 미기록
- **자기신고**: DB 읽기 전용 분석이라 `record_claim`은 미사용(`money_scoreboard` 관례 따라 출력에 3분리 내장 — docstring에 명시), stdout `[claim]` 줄 출력
- **git 명령 0회, DB 쓰기 0회(SELECT만), 리포 밖 파일 0개**

### ③ 검증 출력 (실제 실행)

**pytest** — 변경 파일 4개: `22 passed`
```
tests/test_etf_premium_analysis.py ... 9 passed
tests/test_pairs_arb_research.py ....... 6 passed (합계 22)
```
전체 스위트: **895 passed, 1 failed, 4 skipped** — 실패 1건은 `test_trader_cycle_sample_definition.py::test_unfilled_marker_excluded_from_closed_sample`(오늘 날짜 의존, 본 변경과 무관한 기존 실패 — 이 두 모듈을 import하지 않음).

**`--date 2026-11-20 --index-code KOSPI` 실행 결과**
```
[etf_premium] date=2026-11-20 snaps=385 codes=100 episodes=1049 filtered=37903/39780
  by_regime: {'KOSPI': -5.2148} is_panic_day=True panic_n=1046 normal_n=0 (threshold -1.5%)
[claim] etf_premium_analysis source=39780 claimed=37903 persisted=2 (json+md 리포트)
```
by_regime 수치(JSON):
- `regime: available, index_pct: {"KOSPI": -5.2148}, panic_threshold_pct: -1.5, is_panic_day: true`
- **panic**: +5min n=1046 평균 +0.0149% / +15min n=1027 −0.0069% / +30min n=994 −0.0998% / +60min n=922 −0.2359% (디스카운트 진입 승률 +15min 52.8%)
- **normal**: 전 horizon n=0 (2026-11-20은 패닉일이므로 기대값 null — 0 채움 없음)
- 재실행 후 MD5 동일(`3d4f0026…` / `29ab8b33…`) → 멱등 확인

**pairs_arb `--use-index KOSPI` 실행 결과** (`data/reports/pairs_arb_both_idx_kospi.json`, 기본 breadth 리포트는 미덮어씀)
```
pairs=103 trades=826 | all: exp=-0.1715 | down: n=303 exp=+0.2366 | up: n=523 exp=-0.4079
판정: 하락 레짐(down)에서 기대값 > 0
[claim] pairs_arb_research source=1162877(market_data)+459(index_daily) claimed=103 pairs/826 trades persisted=2
```
breadth 기준 기존 파일과의 수치 차이(down n=655 vs 303 등)는 프록시 교체 효과 + 기존 파일이 2026-11-19 18:35 생성(market_data 2026-11-19 행 추가 전, 95쌍 vs 현재 103쌍)인 데이터 신선도 차이입니다. 기본 경로는 코드 변경 없음(`breadth_index`/`regime_map`/`simulate` 무수정).

### ④ 생성 산출물
- `data/reports/etf_premium.json` / `.md` — by_regime 채워진 상태로 재생성(기본 출력 대상)
- `data/reports/pairs_arb_both_idx_kospi.json` / `.md` — `--use-index` 검증 산출물(기본 파일 보존 목적으로 별도 이름; 불필요하면 삭제 가능)
