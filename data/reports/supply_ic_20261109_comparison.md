# 수급 원천 21거래일 공백 복구 — 피처 IC 동형성 검정 (2026-11-09)

## 사고 경위(실측)
- 호스트가 2026-10-11 ~ 2026-11-09 19:58 꺼져 있었다(원장 마지막 사이클 2026-10-11T20:09, `uptime` 1시간).
- 그 결과 **foreign_institutional(수급 원천)** 이 2026-10-08 에 멈춤 → `source_freshness_gate.py` CRITICAL 21거래일.
  대조: market_data·financial_ratio_features·macro_features·event_features·krx_short_selling·disclosures 는 2026-11-09 정상.
- 파생 테이블 `supply_market_features` 도 원천을 따라 2026-10-08 정지(빌더는 크론에 없음 = CG179 승인 대기).

## 복구 조치(자율 실행)
1. `scripts/kis_supply_backfill.py --stocks <학습유니버스 200종목> --days 30` (setsid, 21:03~21:43)
   - 결과: ok=200 / fail=0 / no_data=0 · 호출≈600 · foreign_institutional 신규 **5,299행** · max trade_date **2026-11-09**
2. `scripts/build_supply_market_features.py` (21:43~21:47, 238.9s)
   - supply_market_features 1,108,353 → **1,167,818행**, max **2026-11-09**
3. `python3 scripts/source_freshness_gate.py` → **"전 원천 정상(지연 <2거래일)"**

## 검정 설계 (scripts/supply_fresh_slice_ic.py)
- 200종목 학습 유니버스, 피처 D일 값 대비 D→D+h 수익률(h=1,5)의 날짜별 스피어만 IC 평균·t.
- 대조 구간 2026-08-01~09-23(37일) vs 복구 구간 2026-10-09~11-06(20일 / h=5 는 16일).
- ⚠ 승격 근거 아님(단일 구간·중첩 창·200종목). 판정은 CG180(5폴드 robust AUC)이 한다.

## 결과 — |IC|≥0.03·|t|≥2 피처의 부호 비교 (h=5)

| 피처 | 대조 8/01~9/23 | 복구 10/09~11/06 | 판정 |
|---|---|---|---|
| momentum_op | **+0.0920** (t 5.47) | **−0.1459** (t −6.19) | **부호 반전** |
| momentum_ni | **+0.0849** (t 5.46) | **−0.1312** (t −4.19) | **부호 반전** |
| bb_position | −0.0872 (t −6.40) | −0.0666 (t −3.50) | 부호 유지(크기 76%) |
| momentum_3_12m | +0.0714 (t 2.46) | −0.0709 (t −2.74) | **부호 반전** |
| relative_strength | −0.0552 (t −2.29) | −0.0284 (t −0.61) | 부호 유지·소멸 |
| institution_net_buy_5d | +0.0452 (t 3.38) | +0.0067 (t 0.23) | 소멸 |
| foreign_net_buy | −0.0445 (t −2.55) | −0.0368 (t −1.40) | 부호 유지·약화 |
| foreign_net_buy_5d | −0.0164 (t −0.92) | **−0.0539** (t −3.29) | 부호 유지·강화 |

h=1: 복구 구간에서 momentum_op −0.0587(t −2.25)·momentum_ni −0.0523(t −2.10) 로 같은 반전, bb_position −0.0304(t −1.15).
시장레벨 3종(krx_advance_decline_ratio·krx_total_trading_value·market_breadth)·short_selling_ratio 는 두 구간 모두 횡단면 상수 → 측정 불가.

## 해석(정직한 한계 포함)
- **정보량 붕괴는 아니다**: 복구 구간 IC 크기(|0.05~0.15|)가 대조보다 오히려 크다 → 새로 채운 21일은 잡음이 아니다.
- **관계 방향이 뒤집혔다**: 모멘텀 3종이 대조 +0.07~0.09 → 복구 −0.07~−0.15. 학습 구간(8~9월)의 모멘텀 관계를 그대로 쓰는 챔피언은 10~11월에 역방향이다.
- 이는 전방 성적표와 방향이 일치한다: h1 top10 −0.4461% vs 전체 +0.4790%, h5 top10 +1.9806% vs 전체 +2.8496% (표본 8일/4일 = 작음).
- 대조/복구가 서로 다른 국면이므로 '피처가 나빠졌다'가 아니라 '국면 의존(비정상성)' 증거로 읽어야 한다.

## 다음 단계
- CG180(5폴드 robust AUC 재기준선, 60분) → pending 승격(신선도 선행조건 충족).
- CG182(신규): 모멘텀 부호 반전이 챔피언 순기대에 미치는 영향 — 모멘텀 계열 중립화 arm vs 현행(다중폴드·돈 지표).
