"""edge_stack(7요인 합의 점수) 순수 함수 테스트 — spec_feed_edge_score.md §4 (8개).

WHY: 엣지 점수는 피드 발행 시 후보마다 부착되어 트레이더측 게이트(spec_trader_edge_gate.md)의
문턱이 된다. 오계산(미래 정보, 경계값 오판, 퇴화 입력)은 진입 품질 필터를 조용히 무력화하므로
손계산이 가능한 합성 봉으로 요인별 0/1 판정을 고정한다. 요인 정의는 백테스트 스펙
(docs/spec_edge_stack_backtest.md §엣지 스택)과 동일 정의(변경 금지).

① 7요인 각각 참/거짓 합성 봉 ② 점수 합(0..7) ③ 미래정보 미사용(마지막 봉을 바꿔도
이전 봉 점수 불변) ④ 30봉 미만 → None+no_history ⑤ high==low 퇴화 ⑥ ATR 소수 자리
(원값 보고·클램프 없음) ⑦ vol_fit 경계값(1.0%/6.0%) ⑧ overheat_avoid 경계(12.0%/25.0%).
기존 테스트 기대값 수정 금지(스펙 §4) — 이 파일은 신규.
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from edge_stack import FACTORS, compute_edge  # noqa: E402

N = 40  # 기본 합성 봉 수(≥30)


def make_bars(closes=None, opens=None, highs=None, lows=None, volumes=None, n=N):
    """합성 봉 생성. 기본: 평탄(close=1000, high=close+10, low=close-10, open=close,
    volume=1000). 평탄 기본값에서 TR=20(ATR14=20), chg=0, 누적=0 이 된다."""
    out = []
    for i in range(n):
        c = 1000.0 if closes is None else float(closes[i])
        h = (c + 10.0) if highs is None else float(highs[i])
        l = (c - 10.0) if lows is None else float(lows[i])
        o = c if opens is None else float(opens[i])
        v = 1000.0 if volumes is None else float(volumes[i])
        out.append({"open": o, "high": h, "low": l, "close": c,
                    "volume": v, "trading_value": v * c})
    return out


def _flat():
    return make_bars()


def _rising():
    """closes = 1000×20 뒤 +10씩 20봉 상승(1010..1200).
    손계산: MA20_t = 1105, MA5_t = 1180, MA20_{t-5} = 1060."""
    return make_bars(closes=[1000.0] * 20 + [1000.0 + 10.0 * i for i in range(1, 21)])


def _sr_false():
    """최근 20봉 안(t-9)에 고가 1500 스파이크 → close(1000)가 0.98×HHV20 미만이고
    HHV20_{t-1} 도 넘지 못한다."""
    highs = [1010.0] * N
    highs[25] = 1500.0
    return make_bars(highs=highs)


def _candle(close_last):
    opens = [1000.0] * N
    highs = [1010.0] * N
    lows = [990.0] * N
    closes = [1000.0] * N
    opens[N - 1], highs[N - 1], lows[N - 1], closes[N - 1] = 1000.0, 1100.0, 1000.0, close_last
    return make_bars(closes=closes, opens=opens, highs=highs, lows=lows)


def _volume(last):
    volumes = [1000.0] * N
    volumes[N - 1] = last
    return make_bars(volumes=volumes)


def _tr(tr):
    """close=10000 고정, TR = high-low = tr 인 평탄 봉(ATR14 = tr)."""
    closes = [10000.0] * N
    half = tr / 2.0
    return make_bars(closes=closes, highs=[c + half for c in closes],
                     lows=[c - half for c in closes])


# ① 7요인 각각 참/거짓 합성 봉 — (요인, 기대값, 생성기)
# 기대값의 근거는 정본(scripts/edge_stack_backtest.py) 함수에 같은 합성 봉을 넣어 실측한 값
# (2026-11-17 — edge_stack.py docstring 하단의 대조 명령·결과와 동일).
FACTOR_CASES = [
    ("trend_dir", True, _rising),
    ("trend_dir", False, _flat),
    ("trend_slope", True, _rising),
    ("trend_slope", False, _flat),
    ("sr_breakout", True, _flat),          # 1000 ≥ 0.98×1010
    ("sr_breakout", False, _sr_false),
    ("candle_quality", True, lambda: _candle(1070.0)),   # body 70/100, 위치 0.7
    ("candle_quality", False, lambda: _candle(1050.0)),  # body 0.5, 위치 0.5
    # 정본 f4 = close_pos*3 >= 2-1e-9 ("상위 1/3" = 2/3-ε) — 2/3 경계 포함/미달
    ("candle_quality", True, lambda: _candle(1000.0 + 200.0 / 3.0)),   # 위치 2/3
    ("candle_quality", False, lambda: _candle(1066.0)),                # 위치 0.66
    ("volume_confirm", True, lambda: _volume(2200.0)),   # 2200/1060 = 2.075 ≥ 2
    ("volume_confirm", False, _flat),
    ("vol_fit", True, lambda: _tr(100.0)),   # ATR14/close = 1.0% (경계 포함)
    ("vol_fit", False, lambda: _tr(50.0)),   # 0.5% 미달
    ("overheat_avoid", True, _flat),
    ("overheat_avoid", False, lambda: make_bars(closes=[1000.0] * (N - 1) + [1150.0])),
]


@pytest.mark.parametrize("factor,expected,builder", FACTOR_CASES)
def test_01_factor_true_false_synthetic(factor, expected, builder):
    res = compute_edge(builder())
    assert res["edge_factors"][factor] is expected


def test_02_score_is_sum_zero_to_seven():
    res = compute_edge(_flat())
    assert set(res["edge_factors"]) == set(FACTORS)
    # 손계산: 평탄 봉 → sr(고가 2% 이내) T, vol_fit(0.02) T, overheat T, 나머지 F = 3
    assert res["edge_score"] == 3
    assert isinstance(res["edge_score"], int)
    assert 0 <= res["edge_score"] <= 7
    assert res["edge_reason"] == "ok"
    assert res["ma20"] == 1000.0
    assert res["hhv20"] == 1010.0
    assert res["atr_pct"] == 0.02


def test_03_no_future_info_previous_bar_unchanged():
    bars = _rising()
    e0 = compute_edge(bars, signal_index=N - 2)          # 신호 t=38
    modified = [dict(b) for b in bars]
    # 급락봉(close 800, 거래량 폭증)으로 교체 — 손계산: t=39 점수 5 → 4
    # (trend_dir F: 800 < MA20 1085, sr F: 800 < 0.98×1200, volume T, vol_fit T, overheat T)
    modified[N - 1] = {"open": 800.0, "high": 820.0, "low": 780.0,
                       "close": 800.0, "volume": 999999.0, "trading_value": 5e9}
    e1 = compute_edge(modified, signal_index=N - 2)      # 마지막 봉을 바꿔도 t=38 점수 불변
    assert e0 == e1
    e_orig = compute_edge(bars)                          # 신호 t=39 는 바뀐다(신호봉 자체)
    e_mod = compute_edge(modified)
    assert e_orig["edge_score"] != e_mod["edge_score"]
    assert e_mod["edge_score"] is not None


def test_04_under_30_bars_no_history():
    res = compute_edge(make_bars(n=29))
    assert res["edge_score"] is None
    assert res["edge_reason"] == "no_history"
    assert res["atr_pct"] is None and res["ma20"] is None and res["hhv20"] is None
    assert compute_edge([])["edge_reason"] == "no_history"


def test_05_degenerate_high_equals_low():
    bars = make_bars()
    bars[N - 1] = {"open": 1000.0, "high": 1000.0, "low": 1000.0, "close": 1000.0,
                   "volume": 1000.0, "trading_value": 1e6}
    res = compute_edge(bars)
    assert res["edge_score"] is None
    assert res["edge_reason"] == "degenerate"
    assert res["edge_factors"] == {}
    # 결측값도 퇴화로 처리한다(분모·이동평균 오염 방지)
    bars2 = make_bars()
    bars2[30]["close"] = None
    assert compute_edge(bars2)["edge_reason"] == "degenerate"


def test_06_atr_pct_six_decimals_no_clamp():
    # TR = 123.456789 → ATR14 = 123.456789 → /10000 = 0.0123456789 → 6자리 반올림
    closes = [10000.0] * N
    res = compute_edge(make_bars(
        closes=closes, highs=[c + 61.7283945 for c in closes],
        lows=[c - 61.7283945 for c in closes]))
    assert res["atr_pct"] == 0.012346
    assert res["atr_pct"] == round(res["atr_pct"], 6)
    assert res["edge_factors"]["vol_fit"] is True
    # 적합 범위(1~6%)를 벗어나도 atr_pct 는 원값을 보고한다(클램프 없음 — 요인만 False)
    res2 = compute_edge(_tr(10000.0))
    assert res2["atr_pct"] == 1.0
    assert res2["edge_factors"]["vol_fit"] is False


def test_07_vol_fit_boundary_1pct_6pct():
    assert compute_edge(_tr(100.0))["edge_factors"]["vol_fit"] is True     # 정확히 1.0%
    assert compute_edge(_tr(600.0))["edge_factors"]["vol_fit"] is True     # 정확히 6.0%
    assert compute_edge(_tr(99.99))["edge_factors"]["vol_fit"] is False    # 0.9999% 미달
    assert compute_edge(_tr(600.01))["edge_factors"]["vol_fit"] is False   # 6.001% 초과


def _overheat(prev_close, t_close, t_minus5):
    closes = [1000.0] * N
    closes[N - 1] = t_close
    closes[N - 2] = prev_close
    closes[N - 6] = t_minus5
    return make_bars(closes=closes)


def test_08_overheat_boundary_12pct_25pct():
    # 정본(edge_stack_backtest) 실측: f7 = (chg*100 <= 12.0) & (chg5*100 <= 25.0), ε 없음.
    # "정확히 12%"(1120/1000)는 0.12 의 이진 부정확으로 chg% = 12.000000000000002 가 되어
    # False 가 된다 — 정의 변경 금지(스펙 §6)라 정본과 같은 행동을 그대로 고정한다.
    assert compute_edge(_overheat(1000.0, 1120.0, 1000.0))["edge_factors"]["overheat_avoid"] is False
    # chg 11.9% → 통과, 12.1% → 과열
    assert compute_edge(_overheat(1000.0, 1119.0, 1000.0))["edge_factors"]["overheat_avoid"] is True
    assert compute_edge(_overheat(1000.0, 1121.0, 1000.0))["edge_factors"]["overheat_avoid"] is False
    # 5일 누적 정확히 25%(1000/800 = 1.25 — 0.25 는 이진 정확, chg 0%) → 통과
    assert compute_edge(_overheat(1000.0, 1000.0, 800.0))["edge_factors"]["overheat_avoid"] is True
    # 5일 누적 25.1% → 과열
    assert compute_edge(_overheat(1000.0, 1000.0, 799.0))["edge_factors"]["overheat_avoid"] is False
