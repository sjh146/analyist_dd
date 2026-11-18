# 스펙 — 뉴스 언급 피처를 패널에 컬럼 패치 (죽은 피처 2단계 ③)

작성 2026-11-19 05:10. 배경(실측): 종목별 뉴스 수집으로 **언급 보유 종목 31 → 193**(패널 200)까지
올렸고 `news_mention_features`(mention_7d/30d/90d, 6,398행)가 생성됐다. 문제는 기존 패널의
뉴스 컬럼(`news_count_5d`·`news_count_20d`)이 옛 소스(news_events, 커버 31종목)에서 계산돼
**상수 0**이라는 것. 전체 재빌드(3시간+) 없이 **해당 컬럼만 교체**해 효과를 측정한다.

## 1. 신규 `scripts/patch_panel_news.py`

```
python3 scripts/patch_panel_news.py --panel PATH --out PATH2 --from 2026-10-07 --to 2026-11-17
                                   [--dry-run] [--report data/reports/panel_news_patch.json]
```
- 입력: 패널 npz(`X`, `feature_names`, `dates`, `codes`, `price`) + `news_mention_features`
- 대상 컬럼(있을 때만): `news_count_5d`, `news_count_20d`
  (없으면 `--append` 로 `mention_7d`·`mention_30d` 신규 컬럼을 append — 두 모드 모두 지원)
- 계산: 각 (code, date) 에 대해 **date 이전(당일 제외) 5·20거래일 누적 언급수**.
  거래일 달력은 패널의 `dates` 를 그대로 사용(패널 행과 정렬 보장). 결측은 **NaN**(0 아님),
  언급 테이블에 없는 종목은 NaN.
- **쓰기 규칙: `--out` 은 원본과 달라야 한다(같으면 exit 2)** — 기준선 보존.
  `np.savez_compressed` 로 저장하고, 저장 후 ①행수 ②`feature_names` 순서 ③비대상 컬럼
  **바이트 동일**(`np.array_equal(..., equal_nan=True)`) 을 자기검증해 리포트에 남긴다.
- 리포트: 컬럼별 `nnz_before`, `nnz_after`, `nonzero_rows`, `mean/std`, `null_ratio` + 자기검증 결과.

## 2. 검증 (실행 출력 원문 첨부)

```
/usr/bin/python3 scripts/patch_panel_news.py --panel services/xgboost-ml/app/models/wf/panel_prod200_v2.npz \
    --out services/xgboost-ml/app/models/wf/panel_prod200_v3.npz --from 2026-10-07 --to 2026-11-17
/usr/bin/python3 scripts/feature_survival_report.py --panel services/xgboost-ml/app/models/wf/panel_prod200_v3.npz \
    --json-out data/reports/feature_survival_v3.json --md-out data/reports/feature_survival_v3.md
```
- 기대: `news_count_5d`/`news_count_20d` 의 `nnz_after` 가 **0.02% → 5% 이상**,
  생존 판정에서 `DEAD_SPARSE` → `ALIVE`(또는 최소 조건부) 이동.
- 비대상 컬럼 바이트 동일이 **False 면 실패**로 보고(패치가 다른 컬럼을 건드렸다는 뜻).

## 3. 테스트 — `tests/test_patch_panel_news.py` (신규 5개)

① as-of 계산(당일 제외, 5거래일 창) ② 결측 NaN 유지(0 아님) ③ 대상 컬럼만 변경·나머지 동일
④ `--out` 이 원본과 같으면 exit 2 ⑤ `--append` 모드에서 feature_names 길이 증가 + 기존 컬럼 보존

## 4. 금지

- 버전관리 금지. 원본 패널 덮어쓰기 금지. 기존 스크립트·테스트 수정 금지.
- DB 쓰기 금지(SELECT 전용). 결측 0 채움 금지. 실주문·브리지·트레이더 코드 무접근.
