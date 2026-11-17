# 스펙 v2 — 자본 제약 시뮬레이터 (실계좌 조건 그대로 + 부분익절/트레일링)

작성 2026-11-18. **사용자 지시**: "내가 말한대로 트레이더 스케일업 해줘" + 진행상황 보고의 다음 단계 1번.
선행: `docs/spec_edge_stack_backtest.md` / `data/reports/edge_stack_backtest.json`(2026-11-17, 신호 14,440건).

## 왜 v2 인가 (v1 의 한계 — 실측)

v1 은 신호 단위로 손익률만 계산했다. 그래서 실계좌와 다른 점이 3개 남았다:
1. **자본 제약 무시**: 자본 245,435원, 종목당 상한 1/3(≈81,811원)이라 고가주는 0주다(실측: 어제 후보 096530 24,200원 → 3주). v1 은 그런 종목도 100% 체결된 것으로 계산했다.
2. **동시 보유·일일 건수 무시**: 동시 3종목·일 3건 한도와 15:00–15:20 강제청산 때문에 실제로는 **못 들어간 신호**가 많다.
3. **MaxDD 측정 불가**: v1 은 전액 재투입 복리로 5,873% 같은 값이 나와 `null` 계약으로 바꿨다. 자본곡선을 실제로 굴려야 MaxDD 가 의미를 갖는다.

## 목표

실계좌 조건을 그대로 넣고 **원화(KRW) 자본곡선**을 굴려, 청산 규칙(고정/부분익절/트레일링)별로
기대값·승률·손익비·MaxDD·체결 건수를 낸다. 판정은 승률이 아니라 **기대값과 자본곡선**이다.

## 1. 신규 `scripts/capital_sim.py`

```
python3 scripts/capital_sim.py [--start 2026-04-01] [--end 2026-11-14] \
    [--equity 245435] [--max-pos 3] [--per-stock-pct 0.3334] [--daily-max 3] \
    [--price-min 1000] [--price-max 50000] [--json-out ...] [--md-out ...] [--quiet]
```
- 입력: `market_data`(SELECT 전용, 127.0.0.1:5434) · 신호 정의는 v1 과 동일(`chg>=3%`·`vol_ratio>=1.5`·`거래대금>=5억`).
- `edge_score` 는 `scripts/edge_stack.py` 의 `compute_edge()` 를 **import 해서** 계산(v1 과 같은 정의, 복붙 금지).
- 진입: 신호일 t → **t+1 시가**. 수량 = `floor(min(per_stock_cap, 남은예산/남은슬롯) / 시가)`.
  - `per_stock_cap = equity_now * per_stock_pct`(기본 1/3). 수량 0이면 **진입 불가로 기록**(skipped_capital).
  - `--price-max` 초과 종목은 후보에서 제외(skipped_price_band).
- 동시 보유 ≤ `--max-pos`, 당일 신규 진입 ≤ `--daily-max`, 보유 중 종목 중복 진입 금지.
- 비용: 왕복 0.21%(v1 과 동일). 슬리피지는 별도 옵션 `--slip-pct 0.0`(기본 0, 보고서에 명시).

### 청산 규칙 4종(각각 독립 실행, 같은 신호·같은 자본)

| id | 규칙 |
|---|---|
| `fixed_1_3` | 손절 −1% / 익절 +3% (v1 최고) |
| `fixed_15_45` | 손절 −1.5% / 익절 +4.5% |
| `partial_1r` | +1R(손절폭만큼 상승)에서 **절반 익절**, 잔량은 +3R 또는 손절, 5세션 타임스탑 |
| `trail_atr` | 손절 −1%, +1R 이후 ATR 트레일(고점 −1×ATR14), 5세션 타임스탑 |

- 일봉엔 장중 순서가 없다 → v1 처럼 worst/best 두 극단을 계산하고 **원화 자본곡선도 두 번** 굴린다
  (보수적=worst 를 기본 보고, best 를 참고). 애매일(둘 다 터치) 비율도 함께 기록.
- 5세션 타임스탑·강제 당일청산은 **프로파일별로 분리**: `--same-day-close`(기본 on, 단타 가정) /
  `--hold-sessions 5`(스윙 가정). 둘 다 돌려 표에 넣는다(단타 vs 스윙 비교).

### 산출 지표(모드별)

`trades, win_pct, avg_win_pct, avg_loss_pct, r_multiple, expectancy_pct, expectancy_krw,
total_pnl_krw, equity_end_krw, maxdd_pct(자본 대비), skipped_capital, skipped_price_band,
ambiguous_pct, avg_hold_sessions`

### 워크포워드

v1 과 같은 분할(2026-04-01~08-15 선택 / 08-16~11-14 홀드아웃). 선택 구간에서 규칙을 고르고
홀드아웃은 재조정 없이 적용. 추천 조건: ①홀드아웃 기대값 > +0.10%/건 ②홀드아웃 총손익 > 0
③표본 ≥100건 ④MaxDD ≤ 15%(자본 대비). **충족 조합이 없으면 "추천 없음"**으로 쓰고 억지 추천 금지.

## 2. 산출물

- `data/reports/capital_sim.json` / `.md` — 실행 시각 미기록(멱등), `data_range`·`params` 만.
- md 구성: ①실계좌 조건(자본·한도·가격대) ②규칙별 표(보수/낙관 두 밴드) ③워크포워드 ④추천(있으면 값,
  없으면 이유) ⑤한계(일봉 순서·슬리피지 미반영·유동성 가정·생존편향).

## 3. 테스트 — `tests/test_capital_sim.py` (신규 8개)

① 정수 수량 내림(예: 81,811원 예산·24,200원 → 3주) ② 가격대 상한 초과 종목 제외
③ 동시 3종목 초과 시 진입 불가 기록 ④ 당일 3건 한도 ⑤ 부분익절(+1R 절반) 손익 손계산
⑥ 트레일링: 고점 갱신 후 −1×ATR 에서 청산 ⑦ MaxDD 계산(자본곡선) 손계산
⑧ 멱등(같은 입력 → 같은 바이트)

## 4. 검증 (실행 출력 원문 첨부)

```
cd /home/jhshi/analyist_dd
/usr/bin/python3 -m pytest tests/test_capital_sim.py -q
/usr/bin/python3 scripts/capital_sim.py --quiet   # 4규칙 × (단타/스윙) × (worst/best)
/usr/bin/python3 -c "
import json;d=json.load(open('data/reports/capital_sim.json'))
print(json.dumps(d['recommendation'],ensure_ascii=False)[:400])
for r in d['results']: print(r['rule'], r.get('hold'), 'EV', r['worst']['expectancy_pct'], 'PnL', r['worst']['total_pnl_krw'], 'MaxDD', r['worst']['maxdd_pct'], 'n', r['worst']['trades'])"
```

## 5. 금지

- `git` 금지(커밋은 오케스트레이터). DB SELECT 전용. `.env` 값 출력 금지. 네트워크 호출 금지.
- 기존 스크립트(`edge_stack.py`·`edge_stack_backtest.py`·`feed_export.py`) 수정 금지 — import 만.
- 실주문·브리지·트레이더 코드 접근 금지. 임계값·규칙을 결과 보고 사후 변경 금지.
- 리포 밖 파일 생성 금지. 기존 테스트 기대값 수정 금지.
