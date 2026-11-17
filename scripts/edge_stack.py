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

요인 정의는 백테스트 스펙(docs/spec_edge_stack_backtest.md §엣지 스택)과 **동일 정의**
(스펙 §6 금지: 정의를 다르게 바꾸지 말 것 — 변경은 새 버전 이름으로만). 신호일 t 시점 정보만
사용(미래 정보 금지). 백테스트 산출물(data/reports/edge_stack_backtest.json)이 아직 없어
(조회: ls data/reports/edge_stack_backtest.json → No such file, 2026-11-17) 이 모듈이
정의의 정본이 되므로, 구현 선택을 전부 주석으로 남겨 백테스트 구현이 따라올 수 있게 한다:

| 요인 | 정의 |
|---|---|
| trend_dir      | close_t > MA20_t AND MA5_t > MA20_t |
| trend_slope    | MA20_t > MA20_{t-5} |
| sr_breakout    | close_t >= 0.98*HHV20_t OR close_t > HHV20_{t-1} (HHV=고가 최대) |
| candle_quality | body/range >= 0.5 AND (close-low)/(high-low) >= 0.667 |
| volume_confirm | volume_t / MA20(volume) >= 2.0 |
| vol_fit        | 0.01 <= ATR14/close_t <= 0.06 |
| overheat_avoid | chg_t <= 12% AND 5일 누적 <= 25% |

구현 선택(백테스트와 공유할 정의):
- MA = 단순이동평균(SMA). MA20_{t-5} = closes[t-24..t-5] 평균(기울기 5일).
- HHV20_t = highs[t-19..t] 최대. HHV20_{t-1} = highs[t-20..t-1] 최대(20일 고점, t-1 기준).
- ATR14 = Wilder 방식: 시드 = 처음 14개 TR 단순평균(TR_0 = high_0-low_0), 이후
  ATR_i = (ATR_{i-1}*13 + TR_i)/14. TR_i = max(high-low, |high-c_{i-1}|, |low-c_{i-1}|).
  (TR 이 일정한 계열에서 SMA14와 같아, 경계 테스트는 손계산 가능.)
- chg_t = close_t/close_{t-1} - 1, 5일 누적 = close_t/close_{t-5} - 1.
- 부동소수점 경계: chg(0.12)·누적(0.25)·vol_fit(0.01/0.06)은 이진 부정확 때문에 1e-9
  허용 폭을 더해 손계산 경계값(예: 1120/1000-1 = 정확히 12%)이 참이 되게 한다.
  0.5·0.667·2.0 은 손계산 값이 리터럴과 같은 double 로 반올림되므로 그대로 비교한다.
- 30봉 미만 → no_history. 신호봉 퇴화(high<=low, close<=0, 값 결측) → degenerate.

사용:
    from edge_stack import compute_edge, FACTORS
    res = compute_edge(bars)  # bars: 날짜 오름차순 list[dict(open,high,low,close,volume,trading_value)]
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


def _wilder_atr14(trs, upto):
    """Wilder ATR14 — 시드 = TR 처음 14개 단순평균, 이후 (전일ATR*13 + TR)/14."""
    atr = sum(trs[:14]) / 14.0
    for i in range(14, upto + 1):
        atr = (atr * 13.0 + trs[i]) / 14.0
    return atr


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

    # True Range(첫 봉은 고가-저가, 이후는 전일 종가 포함 3-way) → Wilder ATR14
    trs = [highs[0] - lows[0]]
    for i in range(1, t + 1):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    atr14 = _wilder_atr14(trs, t)

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
    atr_pct_raw = atr14 / closes[t]
    chg = closes[t] / closes[t - 1] - 1.0
    cum5 = closes[t] / closes[t - 5] - 1.0

    factors = {
        "trend_dir": closes[t] > ma20 and ma5 > ma20,
        "trend_slope": ma20 > ma20_prev5,
        "sr_breakout": closes[t] >= 0.98 * hhv20 or closes[t] > hhv20_prev,
        "candle_quality": body / rng >= 0.5 and candle_pos >= 0.667,
        "volume_confirm": vol_ratio >= 2.0,
        "vol_fit": 0.01 - _EPS <= atr_pct_raw <= 0.06 + _EPS,
        "overheat_avoid": chg <= 0.12 + _EPS and cum5 <= 0.25 + _EPS,
    }
    return {
        "edge_score": int(sum(factors.values())),
        "edge_factors": factors,
        "atr_pct": round(atr_pct_raw, 6),
        "ma20": ma20,
        "hhv20": hhv20,
        "edge_reason": "ok",
    }
