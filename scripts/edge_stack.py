#!/usr/bin/env python3
"""엣지 스택(7요인 합의 점수) 계산 — 순수 함수. DB·네트워크·시계·랜덤 없음.

WHY (docs/spec_feed_edge_score.md, 2026-11-17, 사용자 지시 "트레이더 스케일업"):
엣지는 하나의 지표가 아니라 확률을 쌓는 것 — 추세·지지저항·캔들·거래량·변동성·과열 회피를
결합해 거래 품질을 필터링한다. 이 모듈은 그 7요인 합의 점수(0..7)를 **피드 발행 시**
(scripts/feed_export.py) 후보마다 계산해 부착하고, 트레이더측 게이트(spec_trader_edge_gate.md)가
문턱으로 쓴다.

실측 근거(docs/spec_edge_stack_backtest.md §실측 기준선, 2026-11-17 확인):
실계좌 11건 승률 22.2% · 기대값 −2,943원/건 · 손익비 0.203(역방향) → 병목은 청산 폭이 아니라
**진입 품질**이다. 같은 신호 11,300건 그리드 실측에서도 청산 폭 변경만으로는 + 전환이 안 됐다.
따라서 진입 품질을 다요인 점수로 만들어 게이트한다.

요인 정의는 백테스트와 **동일 정의** — 스펙 §6 금지(정의 변경은 새 버전 이름으로만), 그리고
피드 스펙의 "선행 산출물이 있으면 그것을 정본으로 따른다"에 따라 **scripts/edge_stack_backtest.py
의 edge_factors() 를 정본으로 대조했다**(2026-11-17 16:29 산출물 기준). 대조 결과 채택한
정본 구현 선택(합성 봉으로 백테스트 함수에 넣어 실측 — 조회 명령은 이 파일 하단 주석 참고):
- ATR14 = TR 14일 **단순평균**(rolling(14).mean()). Wilder 평활 아님 —
  백테스트 MD: "스펙이 정의하지 않아 단순평균 채택".
- f4 = body/rng >= 0.5 AND close_pos*3 >= 2-1e-9 (정본 코드 그대로 — "상위 1/3"은
  0.667 이 아니라 2/3-ε 로 판정. close_pos=2/3 합성 봉에서 정본 f4=True 실측).
- f6 = 1.0 <= ATR14/close*100 <= 6.0 (퍼센트 스케일, ε 없음 — 정본 코드 그대로).
- f7 = chg*100 <= 12.0 AND chg5*100 <= 25.0 (퍼센트 스케일, ε 없음). 정본에서
  "정확히 12%" 합성 봉(1120/1000)은 chg% = 12.000000000000002 > 12.0 으로 **False**
  가 된다(0.12 의 이진 부정확 — 정본과 같은 행동을 의도적으로 유지).
- chg = close_t/close_{t-1}-1, chg5(5일 누적) = close_t/close_{t-5}-1 (pct_change 와 동일식).

| 요인 | 정의 |
|---|---|
| trend_dir      | close_t > MA20_t AND MA5_t > MA20_t |
| trend_slope    | MA20_t > MA20_{t-5} |
| sr_breakout    | close_t >= 0.98*HHV20_t OR close_t > HHV20_{t-1} (HHV=고가 최대) |
| candle_quality | body/range >= 0.5 AND (close-low)/(high-low) >= 0.667 |
| volume_confirm | volume_t / MA20(volume) >= 2.0 |
| vol_fit        | 0.01 <= ATR14/close_t <= 0.06 |
| overheat_avoid | chg_t <= 12% AND 5일 누적 <= 25% |

구현 선택(백테스트 정본과 공유할 정의 — 정본 대조 실측은 파일 하단 주석):
- MA = 단순이동평균(SMA). MA20_{t-5} = closes[t-24..t-5] 평균(정본의 ma20.shift(5)).
- HHV20_t = highs[t-19..t] 최대(rolling(20).max). HHV20_{t-1} = highs[t-20..t-1] 최대.
- TR_i = max(high-low, |high-c_{i-1}|, |low-c_{i-1}|), TR_0 = high_0-low_0
  (정본은 shift(1) NaN 을 skipna 로 같은 값). ATR14 = TR 14일 단순평균.
- chg_t = close_t/close_{t-1} - 1, 5일 누적 = close_t/close_{t-5} - 1.
- 30봉 미만 → no_history. 신호봉 퇴화(high<=low, close<=0, 값 결측) → degenerate.

사용:
    from edge_stack import compute_edge, FACTORS
    res = compute_edge(bars)  # bars: 날짜 오름차순 list[dict(open,high,low,close,volume,trading_value)]

정본 대조 실측(2026-11-17, /usr/bin/python3 — 이 모듈·테스트의 기대값 근거):
    import edge_stack_backtest as bt
    df = <합성 40봉 DataFrame(stock_code, trade_date, open_price, high_price, low_price,
                              close_price, volume, trading_value)>
    bt.edge_factors(bt.compute_features(df)).iloc[-1][["f1","f2","f3","f4","f5","f6","f7","score"]]
결과(정본 값):
    평탄: f3·f6·f7 T → score 3 | 상승: f1·f2·f3·f6·f7 T → score 5
    close_pos=2/3 봉: f4 True | chg 정확히 12%(1120/1000): f7 False(12.000000000000002>12.0)
    TR=100·close 10000: f6 True(1.0%) | TR=600: f6 True(6.0%) | TR=99.99: False | TR=600.01: False
"""
from __future__ import annotations

FACTORS = ("trend_dir", "trend_slope", "sr_breakout", "candle_quality",
           "volume_confirm", "vol_fit", "overheat_avoid")
MIN_BARS = 30
_EPS = 1e-9


def _num(value):
    """숫자 변환(문자열·Decimal 허용). 실패·bool·빈 값은 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip().replace(",", "")
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _none_result(reason):
    return {"edge_score": None, "edge_factors": {}, "atr_pct": None,
            "ma20": None, "hhv20": None, "edge_reason": reason}


def _ma(values, back, end):
    return sum(values[end - back + 1: end + 1]) / float(back)


def compute_edge(bars, signal_index=None):
    """bars: 날짜 오름차순 list[dict(open,high,low,close,volume,trading_value)] (≥30봉).

    신호봉은 기본 마지막 봉(t=len(bars)-1). signal_index 를 주면 그 봉을 신호 t 로 보고
    bars[t+1:] 은 사용하지 않는다(미래 정보 차단 — tests/test_edge_stack.py ③ 이 이를 검증).

    반환: {"edge_score": int(0..7)|None, "edge_factors": {name: bool},
           "atr_pct": float|None(소수 6자리), "ma20": float|None, "hhv20": float|None,
           "edge_reason": "ok"|"no_history"|"degenerate"}
    """
    if not bars:
        return _none_result("no_history")
    t = len(bars) - 1 if signal_index is None else int(signal_index)
    if t < 0:
        return _none_result("no_history")
    window = bars[: t + 1]
    if len(window) < MIN_BARS:
        return _none_result("no_history")

    opens = [_num(b.get("open")) for b in window]
    highs = [_num(b.get("high")) for b in window]
    lows = [_num(b.get("low")) for b in window]
    closes = [_num(b.get("close")) for b in window]
    volumes = [_num(b.get("volume")) for b in window]
    if any(v is None for seq in (opens, highs, lows, closes, volumes) for v in seq):
        return _none_result("degenerate")
    if highs[t] <= lows[t] or closes[t] <= 0:
        return _none_result("degenerate")

    # True Range(첫 봉은 고가-저가, 이후는 전일 종가 포함 3-way) → ATR14 = TR 14일 단순평균
    # (정본 edge_stack_backtest.py: rolling(14).mean() — Wilder 아님)
    trs = [highs[0] - lows[0]]
    for i in range(1, t + 1):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    atr14 = _ma(trs, 14, t)

    ma5 = _ma(closes, 5, t)
    ma20 = _ma(closes, 20, t)
    ma20_prev5 = _ma(closes, 20, t - 5)
    hhv20 = max(highs[t - 19: t + 1])
    hhv20_prev = max(highs[t - 20: t])
    ma20_vol = _ma(volumes, 20, t)

    rng = highs[t] - lows[t]
    body = abs(closes[t] - opens[t])
    candle_pos = (closes[t] - lows[t]) / rng
    vol_ratio = volumes[t] / ma20_vol if ma20_vol > 0 else 0.0
    atr_pct_raw = atr14 / closes[t]          # 출력용 비율(0.023 = 2.3%, 스펙 §1)
    atr_pct_pct = atr_pct_raw * 100.0        # 정본 f6 판정은 퍼센트 스케일(ε 없음)
    chg = closes[t] / closes[t - 1] - 1.0
    cum5 = closes[t] / closes[t - 5] - 1.0

    factors = {
        "trend_dir": closes[t] > ma20 and ma5 > ma20,
        "trend_slope": ma20 > ma20_prev5,
        "sr_breakout": closes[t] >= 0.98 * hhv20 or closes[t] > hhv20_prev,
        # 정본: (body/rng >= 0.5) & (close_pos*3 >= 2-1e-9) — "상위 1/3" 은 2/3-ε 판정
        "candle_quality": body / rng >= 0.5 and candle_pos * 3.0 >= 2.0 - _EPS,
        "volume_confirm": vol_ratio >= 2.0,
        "vol_fit": 1.0 <= atr_pct_pct <= 6.0,
        "overheat_avoid": chg * 100.0 <= 12.0 and cum5 * 100.0 <= 25.0,
    }
    return {
        "edge_score": int(sum(factors.values())),
        "edge_factors": factors,
        "atr_pct": round(atr_pct_raw, 6),
        "ma20": ma20,
        "hhv20": hhv20,
        "edge_reason": "ok",
    }
