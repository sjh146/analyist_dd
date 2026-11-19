# 스펙 — 희박 이벤트 피처 재정의 (event_*_5d 54개 → 레벨·추세·묶음)

작성 2026-11-19 23:05. 근거: `event_*_5d` 54개가 nnz<5% 로 DEAD_SPARSE. 이벤트는 원래 드물게
발생하므로 **5일 창 단독으로는 임계를 못 넘는다**. 정의를 "발생 여부" 중심에서
**레벨(20일 평균)·추세(5일/20일)·묶음(자금조달·지배구조·영업·리스크)** 으로 바꿔 재판정한다.

## 원천 (실측)

`event_features` 223,204행 · 2025-06-17~2026-11-18 · 19컬럼
(`event_capital_increase_5d` … `event_treasury_5d`, `disclosure_count_5d`). 패널 200종목 중 192 커버.

## 1. 신규 `scripts/build_event_rolling.py`

```
python3 scripts/build_event_rolling.py [--from 2025-08-01] [--to 2026-11-18] [--dry-run]
        [--out-table event_rolling_features] [--json-out data/reports/event_rolling.json]
```
- 입력은 `event_features` 의 **5일 롤링 카운트**(이미 창합이므로 원천 재집계 금지 — 이중계산 방지).
- 산출(종목·거래일별, as-of: **당일 행 제외**):
  1. `event_level_20d` = 직전 20행의 **총 이벤트 5d 카운트 평균**
  2. `event_trend_5v20` = (직전 5행 평균) / (직전 20행 평균 + 1e-9)
  3. `event_any_60d` = 직전 60행 중 총 카운트 > 0 이면 1
  4. `funding_level_20d` = capital_increase+cb_bw+mna+treasury 의 20행 평균
  5. `governance_level_20d` = stake_change+exec_change
  6. `business_level_20d` = contract+new_product+partnership+realized
  7. `risk_level_20d` = litigation+disaster+regulation+recall
  8. `disclosure_level_20d` = disclosure_count_5d 의 20행 평균
- 저장: `event_rolling_features(stock_code, trade_date, 8개 컬럼)`, PK(stock_code, trade_date), 멱등 upsert.
- 자기신고: `{rows, codes, date_range, nonzero_ratio per column}` + `--dry-run`.
- **창이 모자라면(직전 20행 미만) null** — 0으로 메우지 않는다.

## 2. 신규 `scripts/patch_panel_events.py`

```
python3 scripts/patch_panel_events.py --panel PATH --out PATH2 [--dry-run]
```
- `event_rolling_features` 8개를 패널에 **append**(기존 컬럼 수정 금지, `feature_names` 길이 213→221).
- as-of: 패널 날짜 **이전** 값만. `--out` 이 원본과 같으면 exit 2. 자기검증(행수·기존 컬럼 바이트 동일).

## 3. 테스트 — `tests/test_event_rolling.py` (신규 5개)

① 20행 평균(직전 20, 당일 제외) ② 추세 = 5행평균/20행평균 ③ any_60d 이진화
④ 창 부족 시 null ⑤ 멱등 upsert(같은 입력 2회 → 행수 불변)

## 4. 검증

```
/usr/bin/python3 -m pytest tests/test_event_rolling.py -q
/usr/bin/python3 scripts/build_event_rolling.py --dry-run
/usr/bin/python3 scripts/build_event_rolling.py
/usr/bin/python3 scripts/patch_panel_events.py --panel data/panels/panel_prod200_v4.npz \
    --out data/panels/panel_prod200_v5.npz
/usr/bin/python3 scripts/feature_survival_report.py --panel data/panels/panel_prod200_v5.npz \
    --json-out data/reports/feature_survival_v5.json --md-out data/reports/feature_survival_v5.md
```
- 기대: 신규 8개 중 **5개 이상 nnz ≥ 5%**(ALIVE/조건부).

## 5. 금지

- 버전관리 금지. 원본 패널·기존 테이블 쓰기 금지(신규 테이블 1개만). DB는 기존 테이블 SELECT.
- 창 부족·결측을 0으로 메우지 말 것. 실주문·브리지·트레이더 코드 무접근.
