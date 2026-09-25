R11 구현 완료입니다. 최종 보고:

## R11 거시 피처 16개 부활 — 구현 보고

### 생성 파일 (신규 2개, 기존 파일 수정 없음)
| 파일 | 내용 |
|---|---|
| `scripts/build_macro_features.py` | 거시 피처 빌더 (as-of 조인 + 변화율/스프레드 + 커버리지 갱신 + 자기신고 + SQL ground-truth 자체 검증) |
| `init-scripts/postgres/19_macro_features.sql` | `macro_features` 테이블 DDL (거래일 PK 1열 + 16개 피처 + WHY/규율 주석) |

### 실측 근거 (docstring·DDL 주석에 조회 명령 포함)
- `macro_indicators` 6,261행 = 환율 3종 751×3 + WTI 749 + 기준금리 1,361(일별 캘린더) + 국고채3년/회사채3년 913×2 + CPI/PPI 36×2, 결측값 **0행** → 원천은 충분하고 변환·적재 코드만 없었다 (가설 확인).
- CPI/PPI는 **지수 레벨**(unit=index, 2026-08 CPI 120.05) → `cpi_yoy/ppi_yoy`는 전년동월비로 계산 (실측: 3.09% / 7.93%).
- `feature_coverage` 16개 전부 nonzero_ratio=0 (computed_at 2026-09-24) → 부활 대상 확인.

### as-of 설계 (룩어헤드 금지)
- **일별 지표**(환율·금리·유가·국고채): 관측일 ≤ D 최신 값 (당일 사용).
- **월별 지표 발표 지연**: 발표일 컬럼이 없어(실측 확인) 관측일+고정 지연으로 근사 — CPI +35일(익월 첫째 주 발표), PPI +55일(익월 셋째 주 발표). **실측 검증**: 2026-09-23 행의 ppi_yoy=7.69(7월분) — 8월분(+55일=9/25)은 보이지 않음 ✓.
- `economic_event_count_7d/impact`: 사전 공지 캘린더 `economic_events`(291행)의 (D-6, D] 창만 사용; 캘린더 시작 전 구간은 NULL. impact는 actual/forecast가 291행 전부 NULL(실측)이라 중요도 가중 강도(high=2, medium=1)로 정의.
- `cycle_up/down`: 기준금리 90일 차분 방향 one-hot (실측 변경점 2025-05-29 인하 → 2026-07-16·08-27 인상으로 양쪽 모두 nonzero 구간 존재).
- 변화율 창은 파이프라인 reader(`macro_features.py`)와 동일: 1m=30일, 3m=90일, YoY=12개월. 이력 부족은 NULL(0 위장 금지).

### 검증 실행 결과
```
R11 검증 쿼리(5개 지명 피처): 5  (성공 기준 ≥3 ✓)
커버리지: nonzero 피처 16/16 (fx_usd_krw 1.00 / cpi_yoy 1.00 / yield_spread 1.00 / cycle_up 0.165 / cycle_down 0.124 …)
as-of 자체 검증: SQL 대조 8일×6개 위반 0 + 저장 테이블 future_dated=0
멱등성 재실행: 삭제 314행 → 삽입 314행 (동일 결과)
자기신고(dq_runner_claim): source 6,552 / claimed 314 / persisted 314, asof_violations=0
```

### 설계 메모
- 거시 값은 종목 횡단면 동일 → 종목×일자 격자 대신 **거래일 1열 테이블**(R10의 107만 행 중복 회피). `feature_coverage`의 분모는 R10과 동일한 market_data 격자 전체(1,078,479행 = supply_market_features 행수와 일치 확인)로 계산해 `cross_section_constant_ratio=1`로 정직하게 표시.
- 커버리지 16행은 `computed_at=now()` 단일 시각으로 갱신 — R11 판정 쿼리(MAX(computed_at) 동치)와 호환.
- 훈련 패널 경유 `feature_coverage_report.py`와의 연결은 백로그 R11.note대로 R14 소관 — 파이프라인(`feature_pipeline.py`)은 수정하지 않았습니다.
- git 명령 미실행 — 커밋은 오케스트레이터에 위임.
