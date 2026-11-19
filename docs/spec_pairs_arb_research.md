# 스펙 — 쌍둥이/차익거래 실측 하네스 (하락장 조건 포함)

작성 2026-11-19. 사용자 요구: "차익거래랑 쌍둥이 매매로 **하락장에서도 실제로 돈을 벌 수 있는지** 테스트".
목표: **무자본·룰 기반 백테스트**로 (a) 우선주/보통주·동일기초 ETF 쌍의 스프레드 평균회귀,
(b) 진입 시장 레짐(하락/상승)별 성과를 같은 표본에서 분리 측정한다. 주문·브리지 무접근.

## 데이터 (실측 확인)

- `market_data`(일봉, 2025-08~2026-11, 2,600+ 종목) · `stocks`(stock_name·instrument_type·market)
- **지수 데이터 없음**(2026-11-19 확인: market_data 는 종목만, macro 는 금리·환율)
  → 시장 프록시 = **전 종목 일별 수익률 중앙값의 누적**(breadth index). 문서에 명시.
- ETF 괴리(시장가 vs NAV) 이력은 **2026-11-19부터만** 축적 중 → 이번 하네스의 표본에서 제외,
  대신 `--etf-premium` 모드를 자리만 만들어 두고 데이터 축적 후 측정(추측 금지).

## 1. 신규 `scripts/pairs_arb_research.py`

```
python3 scripts/pairs_arb_research.py [--mode both|long-only] [--z-entry 2.0] [--z-exit 0.5]
        [--z-stop 3.5] [--lookback 60] [--max-hold 20] [--min-adv 100_000_000]
        [--fee-round-trip-pct 0.21] [--slippage-bps 10]
        [--json-out data/reports/pairs_arb.json] [--md-out data/reports/pairs_arb.md]
```
- **쌍 후보 2계열**
  1. `우선주/보통주`: 이름이 `우|우B|우C|2우B|3우B`로 끝나는 종목 → 접미사 제거한 이름의 보통주와 짝.
  2. `동일기초 ETF`: 이름 앞 브랜드(KODEX·TIGER·ARIRANG·KB·한투·SOL·ACE·RISE 등)만 다른 쌍
     (예: `KODEX 200` ↔ `TIGER 200`). `instrument_type`이 ETF인 종목만.
  공통 조건: 양쪽 모두 데이터 ≥120거래일, 최근 60일 평균 거래대금 ≥ `--min-adv`.
- **스프레드**: `log(A) − β·log(B)`, β는 직전 `--lookback`일 회귀(롤링, 당일 제외).
  `z = (spread − mean_lookback) / std_lookback`. 당일 종가까지만 사용(룩어헤드 금지).
- **진입/청산**: `|z| ≥ z_entry` 진입, `|z| ≤ z_exit` 청산, `|z| ≥ z_stop` 손절, `max_hold` 거래일 초과 청산.
  - `--mode both`: 스프레드가 벌어지면 **비싼 쪽 매도 + 싼 쪽 매수**(이론값, 개인 공매도 불가 전제 포함)
  - `--mode long-only`: **싼 쪽만 매수**(개인 제약 반영), 청산은 수렴 시
- **비용**: 왕복 `fee_round_trip_pct`(기본 0.21) + 편도 슬리피지 `slippage_bps`(기본 10) × 다리 수.
- **레짐 분리(핵심)**: 진입일의 시장 프록시 **20일 수익률 < 0 → `down`**, ≥0 → `up`.
  결과를 `all / down / up` 3분할로 각각 집계.
- **산출**: 쌍별·전체
  `n_trades, win_pct, avg_ret_pct(비용후), expectancy_pct, total_pct(단리), max_dd_pct, avg_hold_days`,
  그리고 `down` 표본에서 **expectancy > 0 인지**를 결론 필드로:
  ```json
  {"verdict": {"down_market_positive": true/false, "down_n": .., "down_expectancy_pct": ..,
               "note": "..."}}
  ```
- 멱등(실행 시각 미기록), 결측은 null+사유.

## 2. 테스트 — `tests/test_pairs_arb_research.py` (신규 6개)

① z-score 계산(합성 스프레드) ② 진입/청산 조건(임계 근처) ③ max_hold 강제청산
④ 비용 반영(왕복+슬리피지) ⑤ 레짐 라벨링(프록시 20일 수익률 부호) ⑥ long-only 모드에서
매도 다리가 없음(포지션 방향 검증)

## 3. 실행·검증

```
/usr/bin/python3 -m pytest tests/test_pairs_arb_research.py -q
/usr/bin/python3 scripts/pairs_arb_research.py --mode both      > /tmp/pairs_both.txt
/usr/bin/python3 scripts/pairs_arb_research.py --mode long-only > /tmp/pairs_lo.txt
```
- 산출물: `data/reports/pairs_arb.json|md`(모드별 파일명 분리: `pairs_arb_both.*`, `pairs_arb_long_only.*`)
- 보고에 **쌍 개수·거래 수·down 레짐 기대값**을 반드시 포함.

## 4. 금지

- 버전관리 금지. 실주문·브리지·트레이더 코드 무접근. DB 쓰기 금지(SELECT).
- 지수 데이터를 **추정해서** 만들지 말 것(프록시는 명시적으로 breadth 로만, 사유 기록).
- 기존 스크립트·테스트 수정 금지. 결측 0 채움 금지.
