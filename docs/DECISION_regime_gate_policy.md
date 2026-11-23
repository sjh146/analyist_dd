# 결정: 레짐 게이트 정책 (2026-11-23 낮 1차 → 같은 날 저녁 개정)

- 날짜: 2026-11-23
- 결정자: 사용자 (선택지 1)
- 관련: `docs/spec_regime_filter.md`, `scripts/daytrading_screener.py --regime-block`,
  `cron/daytrading_pipeline.sh`, `docker-compose.yml`(xgboost-ml `data/state` 마운트)

## 배경

2026-11-23 장중에 같은 스크리너가 실행 경로에 따라 다른 결과를 냈다.

- 호스트 실행(11:05 인트라데이): `레짐 게이트 차단: regime_off` → 후보 0건
- 컨테이너 실행(09:10 파이프라인): 같은 날 후보 20건

원인은 코드 차이가 아니라 **입력 파일 가시성**이었다. `scripts/`는 bind mount라
동일한 코드가 돌지만, 게이트 입력인 `data/state/market_regime.json`이 컨테이너에
마운트되지 않아 `_load_regime()`이 `None`을 반환했고, 게이트는 스펙대로
fail-open(파일 없음 → 통과)으로 동작했다.

수리: `docker-compose.yml`의 `xgboost-ml` 서비스에 `./data/state:/app/data/state`
마운트를 추가하고 컨테이너를 재생성했다. 수리 후 컨테이너 dry-run에서
`regime_off: KOSDAQ close<ma20,ret5<=-3%` → 후보 0건을 실측 확인했다.

## 결정

KOSDAQ 레짐이 `risk_off`이면 **단타 후보를 만들지 않는다**(전면 중단).
즉 파이프라인에 `--regime-block off`를 주지 않는다. `cron/daytrading_pipeline.sh`
는 게이트를 켠 기본값으로만 호출하며, `tests/test_pipeline_regime_block_on.py`가
이 호출 형태를 고정한다.

trade-off를 알고 선택했다:
- 잃는 것: risk_off 구간의 단타 기회 전부(현재 KOSDAQ은 MA20 아래, 5일 -7.7%)
- 얻는 것: 하락장에서 기대값 음수인 추격 매매를 구조적으로 차단. 11/23 실측처럼
  지수가 -3.9%인 날에도 테마 급등이 나오지만, 그런 날 단타의 실현 성적은
  경로 전체에서 마이너스였다(daytrading 11건 +2,285원은 예외적 소표본).

보유 포지션 관리·청산과 close/swing 경로는 이 결정의 영향을 받지 않는다.
`risk_off`에서 수익이 난 경우의 보수화(사이즈 0.5배·엣지 +2·+2% 신규 중단)는
`trader_core/market_guard.py`가 계속 담당한다 — 단타 후보가 0건이므로 실제로는
close/swing 진입에 적용된다.

## 재검토 조건

- KOSDAQ이 MA20 위로 회복해 `risk_on`이 되면 자동으로 후보 생성이 재개된다
  (상승장 확장: ATR ≥ 4% 종목은 일일 상한 +2, 엣지 문턱 −1).
- 레짐 게이트를 끄고 싶다면 이 문서를 먼저 갱신하고, `--regime-block off`를
  쓰는 이유(기대값 근거)를 수치로 남긴다.


---

## 개정 (2026-11-23 저녁, 사용자 요청 "내일부터 매매할 수 있게")

낮 결정(risk_off = 단타 전면 중단)은 **폐기**한다. 이유: KOSDAQ이 MA20 아래에
머무는 동안 매매 자체가 0건이 되어, 사용자가 원하는 "매일 돌아가는 매매"가 성립하지
않는다(현 KOSDAQ 620.47 vs MA20 695.66 — 회복까지 수 주가 걸릴 수 있음).

새 정책 — **후보는 항상 만들고, 위험은 트레이더가 조인다.**

- 스크리너/래퍼: `--regime-block ${DAYTRADING_REGIME_BLOCK:-off}` (기본 off = 후보 생성 허용).
  게이트를 다시 켜려면 env `DAYTRADING_REGIME_BLOCK=on` 한 줄이면 된다.
- 트레이더 `risk_off_baseline` (market_guard.evaluate_risk_off_baseline):
  risk_off 이면 언제나(수익 여부 무관) **사이즈 ×0.5 · 엣지 문턱 +2(4→6) · 일일 진입 2건 상한**.
  기존 `bear_guard`(수익 구간 +1% → ×0.5·+2, +2% → 신규 중단)는 그대로 위에 겹친다.
- 실측 근거(2026-11-23 장중 후보 20종목의 edge_score 분포): 1점 24 · 2점 11 · 3점 13 ·
  4점 6 · 5점 4 · 6점 2(3개 스크리너 합산). 즉 엣지 6+는 하루 1~3종목 → 상한 2건과
  맞물려 "0건"이 아니라 "하루 1~2건"이 된다.
- 안전장치(유지): 스프레드 0.3% 초과 차단 · 당일 +15% 이상 급등 차단 · 레짐 파일 마운트.

무효화된 고정 테스트: `tests/test_pipeline_regime_block_on.py`는 "게이트 기본 off +
스크리너 호출에 REGIME_ARG 포함 + data/state 마운트"를 고정하는 형태로 개정했다.
