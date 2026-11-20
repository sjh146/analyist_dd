# 스펙 — 시장 레짐 필터 (코스닥 하락장에서 롱 단타 중단)

작성 2026-11-20. 근거: `docs/EXP_rule_expectancy_verdict.md`
- 2026년 KOSDAQ **−31.7%**(945 → 646), 유니버스 기준선 **−0.691%/일**(비용 후)
- 상승 추격 규칙 초과수익: R3 −0.054% / R4 −0.043% / R5 −0.251% (모두 음)
- 유일한 (+)는 급락 후 매수 R8 **+0.789%**(t 1.38)

즉 **레짐이 나쁠 때 롱 진입을 계속하는 것 자체가 손실의 주원인**이다. 이 스펙은 진입을
멈추는 최소 장치만 만든다(전략 교체는 별도 스펙).

## 1. 신규 `scripts/market_regime.py`

```
python3 scripts/market_regime.py [--index KOSDAQ] [--ma 20] [--asof YYYY-MM-DD] [--json-out data/state/market_regime.json]
```
- `index_daily` 에서 지수 일봉 읽기(신규 테이블, 2026-11-20 백필 완료: KOSPI/KOSDAQ 459일).
- 계산: `close`, `ma(20)`, `ret5`(5거래일 수익률), `state`:
  - `risk_off` = `close < ma20` **또는** `ret5 ≤ -3%`
  - 그 외 `risk_on`
- 출력 JSON: `{asof, index, close, ma20, ret5, state, reason}` + `data/state/` 경로.
- 지수 데이터가 없으면 `state="unknown"` + 사유(0 채움·추정 금지).

## 2. `scripts/daytrading_screener.py` 게이트

- 신규 옵션: `--regime-file PATH`(기본 `data/state/market_regime.json`), `--regime-block on|off`(기본 `on`)
- 동작: 파일을 읽어 `state == "risk_off"` 이고 `asof` 가 오늘(또는 직전 거래일)이면
  **후보를 만들지 않고** 빈 결과를 쓰되 `reason` 에 `"regime_off: KOSDAQ close<ma20"` 같은 사유를 남긴다.
  - ※ 조용히 0건을 내면 "후보 없음"과 구분되지 않으므로 **사유를 반드시 기록**한다.
- `state` 가 `unknown` 이거나 파일이 없으면 **기존 동작 유지**(fail-open) — 단 로그에 경고 1줄.

## 3. 테스트 `tests/test_market_regime.py` (4개)

① close<ma20 → risk_off ② ret5 ≤ −3% → risk_off ③ 둘 다 정상 → risk_on
④ 지수 없음 → unknown + 사유

## 4. 검증

```
/usr/bin/python3 -m pytest tests/test_market_regime.py -q
/usr/bin/python3 scripts/market_regime.py --asof 2026-11-20
/usr/bin/python3 scripts/daytrading_screener.py --top-n 20 --dry-run   # risk_off 면 후보 0 + 사유
```
- 기대: 2026-11-20 KOSDAQ 상태는 **risk_off**(지수 −31.7% YTD) → 스크리너 후보 0건 + `regime_off` 사유.

## 5. 금지

- **크론 등록·라이브 적용 금지**(오케스트레이터가 사람 승인 후 진행).
- 버전관리 금지. 수집기·실주문·트레이더 코드 무접근. 기존 테스트 기대값 수정 금지.
- 지수 결측을 0/추정으로 메우지 말 것.
