"""docs/spec_edge_stack_backtest.md '테스트' 절 계약 6개 — DB 없이 순수 합성 데이터로 검증.

① edge_score 요인별 0/1 계산(합성 OHLC 로 손계산 일치)
② MA/HHV/ATR 미래정보 미사용(마지막 행 데이터를 바꿔도 그 이전 행 점수 불변)
③ p* 유도식(비대칭 포함)
④ worst/best 시뮬레이션 손계산 일치(작은 합성 케이스)
⑤ 문턱별 표본 부족 표시
⑥ 멱등(실행 시각 미기록 → 같은 입력이면 같은 바이트)
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import edge_stack_backtest as esb  # noqa: E402


def _mk(rows):
    df = pd.DataFrame(rows)
    for c in ["open_price", "high_price", "low_price", "close_price", "volume",
              "trading_value"]:
        df[c] = df[c].astype(float)
    return df


def _factor_row(frame):
    """마지막 행의 (f1..f7, score) 정수 튜플."""
    feats = esb.compute_features(frame)
    scored = esb.edge_factors(feats)
    row = scored.iloc[-1]
    return tuple(int(row["f%d" % i]) for i in range(1, 8)) + (int(row["score"]),)


def _frame_up():
    """상승 추세 30행 + 목표 행(마지막). 7요인 전부 1 — 손계산은 테스트 주석 참조."""
    rows = []
    for i in range(30):
        c = 10000.0 + 20 * i
        rows.append({"stock_code": "A", "trade_date": pd.Timestamp("2026-01-05") + pd.Timedelta(days=i),
                     "open_price": c - 8, "high_price": c + 100, "low_price": c - 100,
                     "close_price": c, "volume": 100000, "trading_value": 1e9})
    c = 10600.0
    rows.append({"stock_code": "A", "trade_date": pd.Timestamp("2026-01-05") + pd.Timedelta(days=30),
                 "open_price": c - 160, "high_price": c + 90, "low_price": c - 210,
                 "close_price": c, "volume": 500000, "trading_value": 1e9})
    return _mk(rows)


def _frame_drop():
    """평탄 30행 + 급락 목표 행 — (0,0,0,0,0,0,1), score 1 손계산 케이스."""
    rows = []
    for i in range(30):
        rows.append({"stock_code": "B", "trade_date": pd.Timestamp("2026-01-05") + pd.Timedelta(days=i),
                     "open_price": 10000.0, "high_price": 10001.0, "low_price": 9999.0,
                     "close_price": 10000.0, "volume": 100000, "trading_value": 1e9})
    rows.append({"stock_code": "B", "trade_date": pd.Timestamp("2026-01-05") + pd.Timedelta(days=30),
                 "open_price": 9700.0, "high_price": 9700.0, "low_price": 9500.0,
                 "close_price": 9600.0, "volume": 100000, "trading_value": 1e9})
    return _mk(rows)


def _frame_surge():
    """평탄 30행 + 급등(+13%>12%) 목표 행 — (1,1,1,0,0,0,0), score 3 손계산 케이스."""
    rows = []
    for i in range(30):
        rows.append({"stock_code": "C", "trade_date": pd.Timestamp("2026-01-05") + pd.Timedelta(days=i),
                     "open_price": 10000.0, "high_price": 10001.0, "low_price": 9999.0,
                     "close_price": 10000.0, "volume": 100000, "trading_value": 1e9})
    rows.append({"stock_code": "C", "trade_date": pd.Timestamp("2026-01-05") + pd.Timedelta(days=30),
                 "open_price": 11200.0, "high_price": 11400.0, "low_price": 11100.0,
                 "close_price": 11300.0, "volume": 100000, "trading_value": 1e9})
    return _mk(rows)


def test_edge_score_factors_hand_calculated():
    # ── frame_up 목표 행 손계산(모든 요인 1):
    #   MA20=10410(11..30행 close 평균) → close 10600>10410 ✓, MA5=10560>10410 ✓ → f1=1
    #   MA20_prev5=10310(6..25행 평균) → 10410>10310 → f2=1
    #   HHV20=10690(고가 최대) → 0.98×10690=10476.2 ≤ 10600 → f3=1
    #   body=160/range=300 → 0.533≥0.5 ✓ · close_pos=210/300=0.7≥2/3 ✓ → f4=1
    #   vol20=120000 → 500000/120000=4.17≥2 → f5=1
    #   TR=300(그 전 13행 200) → ATR14=207.14 → 1.95% ∈ [1,6] → f6=1
    #   chg=+0.19%≤12 ✓ · 5일 누적 +0.95%≤25 ✓ → f7=1  → score 7
    assert _factor_row(_frame_up()) == (1, 1, 1, 1, 1, 1, 1, 7)

    # ── frame_drop 목표 행(급락 −4%):
    #   MA20=9980 → close 9600<9980 → f1=0
    #   MA20_prev5=10000 → 9980>10000 거짓 → f2=0
    #   HHV20=10001 → 0.98×10001=9800.98>9600, HHV20_prev=10001>9600 → f3=0
    #   body=100/range=200 → 0.5≥0.5 ✓ 이지만 close_pos=100/200=0.5<2/3 → f4=0
    #   vol_ratio=1.0 → f5=0
    #   TR=500(그 전 13행 2) → ATR14=37.57 → 0.39%<1 → f6=0
    #   chg=−4%≤12 ✓ · 5일 누적 −4%≤25 ✓ → f7=1  → score 1
    assert _factor_row(_frame_drop()) == (0, 0, 0, 0, 0, 0, 1, 1)

    # ── frame_surge 목표 행(급등 +13%):
    #   MA20=10065 → 11300>10065 ✓, MA5=10260>10065 ✓ → f1=1
    #   MA20 10065>10000 → f2=1
    #   HHV20=11400 → 0.98×11400=11172≤11300 → f3=1
    #   body=100/range=300 → 0.333<0.5 → f4=0
    #   vol_ratio=1.0 → f5=0
    #   TR=1400(그 전 13행 2) → ATR14=101.86 → 0.90%<1 → f6=0
    #   chg=+13%>12% → f7=0  → score 3
    assert _factor_row(_frame_surge()) == (1, 1, 1, 0, 0, 0, 0, 3)


def test_edge_score_no_lookahead():
    df = _frame_up()
    s1 = esb.edge_factors(esb.compute_features(df))
    df2 = df.copy()
    # 마지막 행(미래)을 극단적으로 바꿔도 — 마지막 행 자신만 바뀌고 그 이전 행 점수는 불변해야 함
    for c in ["open_price", "high_price", "low_price", "close_price", "volume"]:
        df2.loc[df2.index[-1], c] = df2.loc[df2.index[-1], c] * 2.0
    s2 = esb.edge_factors(esb.compute_features(df2))
    cols = ["f%d" % i for i in range(1, 8)] + ["score"]
    pd.testing.assert_frame_equal(s1[cols].iloc[:-1], s2[cols].iloc[:-1])


def test_breakeven_formula_asymmetric():
    # 스펙 기준선과 교차검증: ±1% → 60.5% · ±1.5% → 57.0% (스펙 실측 기준선 값)
    assert esb.breakeven_win_pct(1.0, 1.0).item() == pytest.approx(60.5)
    assert esb.breakeven_win_pct(1.5, 1.5).item() == pytest.approx(57.0)
    # 비대칭: p* = (loss+c)/(loss+win) = (1+0.21)/(1+2)×100 = 40.333…%
    assert esb.breakeven_win_pct(1.0, 2.0).item() == pytest.approx(1.21 / 3 * 100)
    assert esb.breakeven_win_pct(1.5, 4.5).item() == pytest.approx(1.71 / 6 * 100)
    # 유도 검증: p* 에서 기대값(p*(tp−c) − (1−p)*(stop+c))이 정확히 0
    for stop, tp in [(1.0, 1.0), (1.5, 4.5), (1.5, 6.0), (2.0, 6.0)]:
        p = esb.breakeven_win_pct(stop, tp).item() / 100.0
        ev = p * (tp - esb.FEE_PCT) - (1 - p) * (stop + esb.FEE_PCT)
        assert ev == pytest.approx(0.0, abs=1e-9)


def test_simulate_worst_best_hand_calculated():
    e = np.array([10000.0, 10000.0, 10000.0, 10000.0])
    hi = np.array([10500.0, 10100.0, 10300.0, 10450.0])
    lo = np.array([9800.0, 9700.0, 9950.0, 9950.0])
    cl = np.array([9900.0, 9800.0, 9980.0, 10050.0])
    res = esb.simulate(e, hi, lo, cl, stop_pct=1.0, tp_pct=4.0)
    w, b = res["worst"], res["best"]
    # 손계산(손절가 9900, 익절가 10400, 왕복비용 0.21%):
    #   1행: 저가 9800≤9900 & 고가 10500≥10400 둘 다 터치 → worst −1.21 / best +3.79
    #   2행: 손절만 터치 → −1.21 / −1.21
    #   3행: 둘 다 미터치 → 종가 9980 → −0.2%−0.21 = −0.41 / −0.41
    #   4행: 익절만 터치 → +3.79 / +3.79
    # worst net = [−1.21, −1.21, −0.41, +3.79]  (통계는 4자리 반올림 계약 → abs=1e-4)
    assert w["n"] == 4 and b["n"] == 4
    assert w["win_pct"] == pytest.approx(25.0, abs=1e-4)
    assert w["avg_pct"] == pytest.approx(0.24, abs=1e-4)
    assert w["sum_pp"] == pytest.approx(0.96, abs=1e-4)
    assert w["avg_win_pct"] == pytest.approx(3.79, abs=1e-4)
    assert w["avg_loss_pct"] == pytest.approx(2.83 / 3, abs=1e-4)   # (1.21+1.21+0.41)/3
    assert w["r"] == pytest.approx(3.79 / (2.83 / 3), abs=1e-4)
    assert w["profit_factor"] == pytest.approx(3.79 / 2.83, abs=1e-4)
    # MaxDD 는 이 스크립트에서 내지 않는다(2026-11-17 정정): 9,500건 복리 곡선은 5,873% 같은
    # 비현실 값이 되고, 자본 제약 시뮬레이터가 정본이다 → null 계약.
    assert w["maxdd_pct"] is None
    assert w["tp_rate"] == pytest.approx(50.0, abs=1e-4)
    assert w["sl_rate"] == pytest.approx(50.0, abs=1e-4)
    assert w["ambiguous"] == pytest.approx(25.0, abs=1e-4)
    # best net = [+3.79, −1.21, −0.41, +3.79]
    assert b["win_pct"] == pytest.approx(50.0, abs=1e-4)
    assert b["avg_pct"] == pytest.approx(1.49, abs=1e-4)
    assert b["avg_loss_pct"] == pytest.approx(1.62 / 2, abs=1e-4)
    # eq = [1.0379, 1.0253, 1.0211, 1.0599] → dd = 1.0211/1.0379−1 = −1.615%
    assert b["maxdd_pct"] is None
    # 중립 = (worst+best)/2
    assert (w["avg_pct"] + b["avg_pct"]) / 2 == pytest.approx(0.865, abs=1e-4)
    assert (w["win_pct"] + b["win_pct"]) / 2 == pytest.approx(37.5, abs=1e-4)


def test_sample_insufficiency_verdict():
    v_small = esb.compose_verdict(n=42, c1=True, c2=False, c3=True, c4=True)
    assert "표본 부족" in v_small and "42" in v_small
    v_ok = esb.compose_verdict(n=500, c1=True, c2=True, c3=True, c4=True,
                               pick_rank=None, recommended=True,
                               sel_ev=0.34, sel_n=500, hold_ev=0.21,
                               sel_win=31.2, pstar=28.5)
    assert "표본 부족" not in v_ok
    assert "→ 추천" in v_ok and "✓" in v_ok
    v_fail = esb.compose_verdict(n=500, c1=False, c2=True, c3=False, c4=True,
                                 sel_ev=0.02, sel_n=500, hold_ev=-0.15,
                                 sel_win=31.2, pstar=28.5)
    assert "추천조건 미충족" in v_fail and "✗" in v_fail


def _frame_report():
    """합성 다종목 신호 프레임 — 멱등 테스트용(신호 3종목×3일, 선택 2·홀드아웃 1)."""
    frames = []
    for code in ["A", "B", "C"]:
        rows, close = [], 10000.0
        d0 = pd.Timestamp("2026-03-01")
        for i in range(80):
            spike = i in (30, 48, 70)          # 신호일: chg +4.5%, 거래량 4배
            close = close * 1.004 if not spike else close * 1.045
            vol = 400000.0 if spike else 100000.0
            rows.append({"stock_code": code, "trade_date": d0 + pd.Timedelta(days=3 * i),
                         "open_price": close * 0.998, "high_price": close * 1.006,
                         "low_price": close * 0.994, "close_price": close,
                         "volume": vol, "trading_value": close * vol})
        frames.append(_mk(rows))
    return pd.concat(frames, ignore_index=True)


def test_idempotent_output_bytes():
    df = esb.compute_features(_frame_report())
    df = esb.edge_factors(df)
    raw, sig = esb.signal_frame(df, "2026-04-01", "2026-11-14")
    assert len(sig) >= 3  # 신호가 실제로 잡히는지(테스트 프레임 검증)
    cfg = {"json_out": "data/reports/edge_stack_backtest.json",
           "md_out": "data/reports/edge_stack_backtest.md"}
    r1 = esb.build_report(sig, "2026-04-01", "2026-11-14", cfg, n_loaded=len(df),
                          raw_n=len(raw))
    r2 = esb.build_report(sig, "2026-04-01", "2026-11-14", cfg, n_loaded=len(df),
                          raw_n=len(raw))
    b1 = json.dumps(r1, ensure_ascii=False, indent=2, sort_keys=True)
    b2 = json.dumps(r2, ensure_ascii=False, indent=2, sort_keys=True)
    assert b1 == b2                                  # 같은 입력 → 같은 바이트
    assert "generated_at" not in b1 and "run_at" not in b1   # 실행 시각 미기록
    assert r1["claim"]["runner"] == "edge_stack_backtest"
