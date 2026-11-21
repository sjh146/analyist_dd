# DECISION — 커버리지 착시 알림의 인구를 '모델 창(120일)'으로 분리 (R38 B안)

- 결정일: 2026-11-21 22:2x KST
- 승인: 사용자 승인("승인할게") + 리뷰보드 대상 항목(R38 이 스스로 "A·B 는 알림 인구 변경이라
  리뷰보드 승인 사항"이라 명시). 프로토콜 락 없음(실험 아님, 관측 정의 변경).
- 대체: R38 의 A안(빌더 4종의 `window_days` 기록을 모델 창 120거래일로 고정 + 1회 전량 재계산)

## 무엇을 바꿨나

| 구분 | 이전 | 이후 |
|---|---|---|
| 알림 지표 | `dq_feature_coverage_illusion_count` (전 창 인구) > 45 | `dq_feature_coverage_illusion_count_model_window` > 30 |
| 전 창 인구값 | 알림 대상 | 정보용(문턱 없음) |
| dq_snapshot SPECS | 45 / 50 | 모델 창 30 / 35, 전 창 None |
| Prometheus 규칙 | `CoverageIllusion` expr 교체 | `for: 1h` 유지 |

모델 창 인구 정의: `window_days = 120 AND nonzero_ratio > 0` 이면서
`nonzero_ratio_naive - nonzero_ratio_honest > 0.10`.

## 왜 A안이 아니라 B안인가

A안은 **빌더의 로드 창을 120거래일로 조인다**는 뜻인데, 이 코드베이스는 그 반대를 위해
설계돼 있다 — `scripts/build_supply_market_features.py:109-116` 이 "격자 하한(since)만 로드하면
`shift(252)` 가 전부 NaN 이 되어 12개월 모멘텀이 죽는다"며 로드 창을 격자 하한과 **분리**해 둔다
(R43 이 같은 이유로 등록돼 있다). A안대로 조이면 `momentum_3_12m` 이 조용히 죽는다.

B안은 데이터·피처 계산을 건드리지 않고 **관측 인구만** 모델이 실제로 쓰는 창으로 좁힌다.

## 근거 실측 (2026-11-21 22:1x)

- `feature_coverage` 202행 / 모델 창(120일) 131행 / 창 가짓수 4(42·44·521·522일)
- 착시>0.10: 전 창 **61** / 모델 창 **26** / 모델 창 아닌 행 **71**
- Prometheus 반영 후 실측: `dq_feature_coverage_illusion_count_model_window = 26`,
  `dq_feature_coverage_illusion_count = 61`, `CoverageIllusion` 규칙 state=inactive
- 과거 26↔62 진동의 원인은 데이터가 아니라 라벨 드리프트(야간 빌더가 격자 스팬으로 덮어씀)

## 문턱 근거

모델 창 기준선 **26**(실측) → warn 30 / breach 35. R29 선례(41 → 45/50, +4/+9)와 동일한 여유.
기준선 아래로 잡으면 상시 발화한다.

## 롤백

1. `config/prometheus/alert.dq.rules.yml` 의 `CoverageIllusion` expr 을
   `dq_feature_coverage_illusion_count > 45` 로 되돌린다.
2. `scripts/dq_snapshot.py` SPECS 의 두 줄을 원복(전 창 45/50, 모델 창 None).
3. Prometheus SIGHUP(또는 재기동) + exporter 는 그대로 두어도 된다(신규 컬럼은 추가형).

## 남은 것

- 전체 인구값이 다시 악화되는지(모델 창은 정상인데 전 창만 증가)는
  `dq_feature_non_model_window_rows`(ALWAYS_REPORT, 0 이면 침묵)와
  `dq_feature_window_variants_count` 로 계속 본다 — 이 두 값이 오르면 야간 빌더가 창을 다시 쓴 것이다.
- 라벨 드리프트 자체(빌더가 `window_days` 를 격자 스팬으로 기록)를 없애는 것은 별건(R43 계열).
