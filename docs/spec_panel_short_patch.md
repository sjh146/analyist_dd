# 스펙 — 공매도 비중 피처를 패널에 컬럼 패치 (short_selling_ratio)

작성 2026-11-19 16:10. 배경(실측): `short_selling_ratio` 는 패널에서 `DEAD_SPARSE`(nnz 3.4%)인데,
원천 `krx_short_selling` 이 **308종목·패널 26/200** 만 덮고 있어서다(공매도 잔고는 원천 부재 → 폐기,
`docs/spec_feature_source_decisions.md`). 지금 전 종목 백필이 돌고 있으므로, 끝난 뒤
**해당 컬럼만 교체**해 재판정한다(전체 재빌드 3시간 회피).

## 1. 신규 `scripts/patch_panel_short.py`

```
python3 scripts/patch_panel_short.py --panel PATH --out PATH2 [--dry-run]
        [--column short_selling_ratio] [--report data/reports/panel_short_patch.json]
```
- 원천: `krx_short_selling(trade_date, stock_code, short_volume, total_volume, short_ratio)`
- 값 정의(빌더 계약과 동일하게): `short_volume / total_volume` (둘 다 0보다 클 때),
  없으면 `short_ratio/100`(원천이 %로 줄 때), 둘 다 없으면 **NaN**(0 채움 금지)
- **as-of**: 패널의 각 `(code, date)` 에 대해 `trade_date <= date` 인 **가장 최근** 공매도 행.
  당일 데이터가 장 종료 후 확정되는 특성상 당일 값을 쓰면 룩어헤드가 될 수 있으므로
  `--lag-days 1`(기본 1거래일)로 **직전 거래일까지**만 사용한다(근거: KRX 공매도는 D+1 공표).
- 쓰기: `--out` 은 원본과 달라야 함(같으면 exit 2). 저장 후 자기검증:
  ①행수 ②`feature_names` 순서 ③**비대상 컬럼 바이트 동일**(`np.array_equal(equal_nan=True)`)
- 리포트: `nnz_before`, `nnz_after`, `nonzero_rows`, `null_ratio`, `source_rows`, `codes_covered`.

## 2. 검증

```
/usr/bin/python3 -m pytest tests/test_patch_panel_short.py -q
/usr/bin/python3 scripts/patch_panel_short.py --panel data/panels/panel_prod200_v3.npz \
    --out data/panels/panel_prod200_v4.npz
/usr/bin/python3 scripts/feature_survival_report.py --panel data/panels/panel_prod200_v4.npz \
    --json-out data/reports/feature_survival_v4.json --md-out data/reports/feature_survival_v4.md
```
- 기대: `short_selling_ratio` nnz **3.4% → 10% 이상**(백필 커버리지에 따라 다름),
  생존 클래스 `DEAD_SPARSE → ALIVE` 또는 조건부.
- 비대상 컬럼 바이트 동일이 False 면 **실패로 보고**.

## 3. 테스트 — `tests/test_patch_panel_short.py` (신규 4개)

① as-of(직전 거래일까지만 사용, 당일 제외) ② short_volume/total_volume 계산·total 0이면 NaN
③ `--out` 이 원본과 같으면 exit 2 ④ 비대상 컬럼 불변(패치 전후 비교)

## 4. 금지

- 버전관리 금지. 원본 패널 덮어쓰기 금지. 기존 스크립트·테스트 수정 금지.
- DB 쓰기 금지(SELECT 전용). 결측 0 채움 금지. 실주문·브리지·트레이더 코드 무접근.
