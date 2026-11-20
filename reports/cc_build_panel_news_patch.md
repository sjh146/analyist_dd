구현 완료. 스펙 `docs/spec_panel_news_patch.md` 를 읽고 **신규 2개 파일만** 만들었습니다(기존 파일 수정 0건, git 0회, DB 쓰기 0회, 원본 패널 접근은 읽기 전용).

## 산출물

- **`scripts/patch_panel_news.py`** — 패널 뉴스 컬럼 패치 러너 (교체 모드 + `--append` 모드, `--dry-run`, `--report`)
- **`tests/test_patch_panel_news.py`** — 스펙 §3 계약 5개 그대로 ①as-of(당일 제외·5거래일 창) ②결측 NaN ③대상 컬럼만 변경 ④`--out`==원본 → exit 2 ⑤append 시 feature_names +2 & 기존 컬럼 보존

## 실측 근거 (docstring 에 조회 명령과 함께 기록, 규칙 ①②)

| 조회 | 결과 |
|---|---|
| `np.load('...panel_prod200_v2.npz')` | X (61640,213) float32, `news_count_5d`(idx 84)·`news_count_20d`(idx 83) **nonzero 각 12 = 0.0195%**, NaN 0 |
| `SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM news_mention_features` | 6,398행 · 2026-08-19~11-18 · 193종목 |
| `SELECT COUNT(*), MIN(published_date), ... FROM news_stock_mentions` | 9,384행 · 2026-08-19~11-18 · 193종목 |
| 창 2026-10-07~11-17 | 패널 5,773행 · 29거래일 · 200종목, 그리드 없는 종목 7개(093640 등) |

## 설계 판단 (스펙이 모호한 곳 — 오케스트레이터 참고용)

- **교체 값의 원천**: "5·20거래일 누적 언급수"는 일별 언급 건수가 필요하므로 `news_stock_mentions` 를 (code, date)로 GROUP BY 해 사용했습니다. `news_mention_features` 는 **커버리지 판정 기준**(그리드에 없는 (code,date) → NaN, "언급 테이블에 없는 종목은 NaN" 규칙)과 **append 모드 값 원천**(mention_7d/30d 조인)으로 씁니다 — 두 테이블 모두 SELECT 전용.
- **창 밖 행**: 교체 모드에서는 **원본 유지**(기준선 보존 — `--from/--to` 가 패치 범위), append 모드 신규 컬럼은 창 밖 NaN. 창이 수집 시작일(2026-08-19) 이전으로 넘어가는 행도 NaN(부분 정보를 0으로 위장하지 않음 — 실측으로 그런 행이 5d 5행, 20d 32행 있음: 예 `008600` 2026-10-12, 20번째 이전 거래일 2026-02-26).
- **자기신고**: 스펙 §4 "DB 쓰기 금지"라 `dq_claim.record_claim`(INSERT)은 금지 → `feature_survival_report.py` 전례로 리포트 JSON `claim` 키에 3분리(소스 수신/파서 생성/실제 저장=디스크 재독해 대조) 기록.
- **멱등**: 실행 시각 미포함 — 같은 입력 → 같은 npz·JSON 바이트. `--out` 은 임시파일 + `os.replace` 원자 덮어쓰기.

## 테스트 중 잡아 고친 버그 3건 (자기검증이 실제로 작동함)

1. `compute_trading_day_counts(dates, codes, ...)` 호출 인자 순서 스왑 → 전 셀 NaN. 수정.
2. 교체 모드에서 `vals`(창 밖 NaN)를 **컬럼 전체**에 대입 → 기준선 파괴. `verify: outside_window=False` 가 잡아냄 → `new_X[mask, idx]` 로 창 내 행만 쓰도록 수정. (스펙이 요구한 "비대상 컬럼 바이트 동일" 검증이 설계 결함을 막은 사례)
3. 테스트 손계산 오류 2건(언급 3건 누락, 코드 블록 경계 넘는 슬라이싱) 수정.

## 검증 출력 (규칙 ⑦)

```
$ /usr/bin/python3 -m pytest tests/test_patch_panel_news.py tests/test_feature_survival.py tests/test_news_stock_collect.py -q
17 passed in 0.74s        ← 신규 5개 전부 통과

$ python3 scripts/patch_panel_news.py --help   → rc=0 (CLI 정상)
$ --dry-run 스모크(합성 패널) → rc=0, out.npz/report 미생성 확인
```

## 본 실행(오케스트레이터) 기대 수치 — 실제 패널+DB 로 읽기 전용 프로브

`run_patch`(순수 함수)를 실제 데이터로 실행(쓰기 없음):

```
news_count_5d (replace,k=5):  nnz_before=12  nnz_after=3915 (6.351%)  changed=4123  null=208  null_ratio=0.0034
news_count_20d(replace,k=20): nnz_before=12  nnz_after=4716 (7.651%)  changed=4951  null=235  null_ratio=0.0038
verify(memory): rows=True names=True non_target=True outside_window=True checks_failed=0
```

스펙 기대(**nnz 0.02% → 5% 이상**) 충족: 6.35% / 7.65%. NaN(결측)은 208=203(그리드 없는 종목 7개)+5(창이 수집 전), 235=203+32 로 정확히 설명됨. 패치 후 생존 판정도 `DEAD_SPARSE` → (coverage_days 100%·nnz≥5%·xsec_const 93.8%<95%) **ALIVE** 경로가 예상됩니다. 본 실행은 하지 않았고(지시대로 테스트만), `--report` 기본 경로는 `data/reports/panel_news_patch.json` 입니다.
