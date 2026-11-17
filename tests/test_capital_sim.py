"""docs/spec_capital_sim.md '테스트' 절 계약 8개 — DB 없이 순수 합성 데이터로 검증.

① 정수 수량 내림(81,811원 예산·24,200원 → 3주)  ② 가격대 상한 초과 종목 제외
③ 동시 3종목 초과 시 진입 불가 기록              ④ 당일 3건 한도
⑤ 부분익절(+1R 절반) 손익 손계산                 ⑥ 트레일링: 고점 갱신 후 −1×ATR 에서 청산
⑦ MaxDD 계산(자본곡선) 손계산                    ⑧ 멱등(같은 입력 → 같은 바이트)

모든 테스트는 합성 OHLC·자본만 사용한다(DB 접속 없음 — 시뮬레이터 순수 함수 계약).
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import capital_sim as cs  # noqa: E402

D0 = pd.Timestamp("2026-01-05")


def _mk_bars(code, closes, highs=None, lows=None, atrs=None, d0=D0):
    """합성 종목 봉. 기본값: 고가=종가+100, 저가=종가−99(진입 10,000 기준 −1% 손절 9,900
    위에 있음 → 손절 미터치), ATR14=100."""
    n = len(closes)
    dates = [d0 + pd.Timedelta(days=i) for i in range(n)]
    highs = [c + 100.0 for c in closes] if highs is None else highs
    lows = [c - 99.0 for c in closes] if lows is None else lows
    atrs = [100.0] * n if atrs is None else atrs
    return {code: {"dates": np.array(dates, dtype="datetime64[ns]"),
                   "high": np.array(highs, dtype=float),
                   "low": np.array(lows, dtype=float),
                   "close": np.array(closes, dtype=float),
                   "atr14": np.array(atrs, dtype=float)}}


def _sig(idx, code, entry_ts, px=10000.0, score=5):
    return {"sig_idx": idx, "stock": code,
            "sig_date": entry_ts - pd.Timedelta(days=1),
            "entry_date": entry_ts, "entry_open": float(px), "edge_score": score}


def _kw(dates, **over):
    kw = dict(equity0=100000.0, max_pos=1, per_stock_pct=1.0, daily_max=3,
              price_min=1000.0, price_max=50000.0, dates=dates)
    kw.update(over)
    return kw


# ① 정수 수량 내림 — 스펙 예시 그대로: 81,811원 예산·24,200원 → 3주
def test_size_floor_hand_calculated():
    budget = cs.entry_budget(equity_now=245435.0, cash=245435.0, slots_used=0,
                             max_pos=3, per_stock_pct=0.3334)
    # 손계산: min(245,435×0.3334=81,828.03, 245,435/3=81,811.67) = 81,811.67
    #   → floor(81,811.67/24,200) = floor(3.3806) = 3주
    assert budget == pytest.approx(245435.0 / 3.0, abs=0.01)
    assert cs.size_qty(24200.0, budget) == 3
    assert cs.size_qty(24200.0, 2000.0) == 0      # 예산 부족 → 0주(진입 불가)
    assert cs.size_qty(0.0, budget) == 0          # 비정상 가격 → 0주


# ② 가격대 상한 초과 종목 제외 (skipped_price_band 기록)
def test_price_band_upper_excluded():
    dates = [D0 + pd.Timedelta(days=i) for i in range(4)]
    bars = _mk_bars("A", [10000.0] * 4)
    res = cs.run_capital_sim([_sig(0, "A", dates[1], px=60000.0)], bars,
                             "fixed_1_3", "same_day", "worst", **_kw(dates))
    assert res["metrics"]["trades"] == 0
    assert res["metrics"]["skipped_price_band"] == 1
    # 하한 미만(진입가 900원)도 같은 버킷으로 제외
    res2 = cs.run_capital_sim([_sig(0, "A", dates[1], px=900.0)], bars,
                              "fixed_1_3", "same_day", "worst", **_kw(dates))
    assert res2["metrics"]["trades"] == 0
    assert res2["metrics"]["skipped_price_band"] == 1


# ③ 동시 3종목 초과 시 진입 불가 기록 (skipped_slots)
def test_max_pos_slots_recorded():
    dates = [D0 + pd.Timedelta(days=i) for i in range(8)]
    bars = {}
    for code in "ABCD":
        bars.update(_mk_bars(code, [10000.0 + i for i in range(8)]))
    sigs = [_sig(0, "A", dates[1]), _sig(1, "B", dates[1]), _sig(2, "C", dates[1]),
            _sig(3, "D", dates[2])]   # D 는 A·B·C 가 스윙 보유 중인 다음 날 진입 시도
    res = cs.run_capital_sim(sigs, bars, "fixed_1_3", "swing5", "worst",
                             **_kw(dates, equity0=245435.0, max_pos=3,
                                   per_stock_pct=0.3334, daily_max=10,
                                   hold_sessions=5))
    assert res["metrics"]["trades"] == 3
    assert res["metrics"]["skipped_slots"] == 1


# ④ 당일 3건 한도 (skipped_daily_max)
def test_daily_limit_recorded():
    dates = [D0 + pd.Timedelta(days=i) for i in range(4)]
    bars = {}
    for code in "ABCD":
        bars.update(_mk_bars(code, [10000.0] * 4))
    sigs = [_sig(0, "A", dates[1]), _sig(1, "B", dates[1]), _sig(2, "C", dates[1]),
            _sig(3, "D", dates[1])]   # 같은 날 4번째 신규 진입
    res = cs.run_capital_sim(sigs, bars, "fixed_1_3", "same_day", "worst",
                             **_kw(dates, equity0=245435.0, max_pos=10,
                                   per_stock_pct=0.3334, daily_max=3))
    assert res["metrics"]["trades"] == 3
    assert res["metrics"]["skipped_daily_max"] == 1


# ⑤ 부분익절(+1R 절반) 손익 손계산
def test_partial_1r_half_hand_calculated():
    dates = [D0 + pd.Timedelta(days=i) for i in range(3)]
    # 진입 10,000 · +1R=10,100 · +3R=10,300 · 손절 9,900
    bars = _mk_bars("A", closes=[10000.0, 10350.0, 10100.0],
                    highs=[10090.0, 10400.0, 10200.0],
                    lows=[9990.0, 9950.0, 10050.0])
    sigs = [_sig(0, "A", dates[1])]
    kw = _kw(dates)
    res = cs.run_capital_sim(sigs, bars, "partial_1r", "same_day", "worst", **kw)
    m = res["metrics"]
    # 손계산: 예산 100,000원 → qty 10. 고가 10,400 ≥ +1R(10,100) → 절반 5주 @10,100,
    #   고가 ≥ +3R(10,300) → 잔량 5주 @10,300. 수익 = 5×100 + 5×300 = 2,000원
    #   − 왕복 비용 0.21%×100,000 = 210원 → 순손익 +1,790원 (ret +1.79%)
    assert m["trades"] == 1
    assert m["total_pnl_krw"] == pytest.approx(1790.0, abs=0.01)
    assert m["expectancy_pct"] == pytest.approx(1.79, abs=1e-4)
    assert m["win_pct"] == 100.0
    assert m["n_partial_exits"] == 1
    assert res["trades"][0]["exits"] == [["partial_1r", 10100.0, 5],
                                         ["target_3r", 10300.0, 5]]
    # 손절 미터치 → best 도 같은 경로 → 애매 0%
    best = cs.run_capital_sim(sigs, bars, "partial_1r", "same_day", "best", **kw)
    assert cs.ambiguous_pct(res["trades"], best["trades"]) == 0.0

    # 손절·+1R 동시 터치: worst=전량 −1%, best=절반 +1R + 잔량 −1% → 애매 100%
    bars2 = _mk_bars("A", closes=[10000.0, 10050.0, 10050.0],
                     highs=[10090.0, 10150.0, 10090.0],
                     lows=[9990.0, 9800.0, 9990.0])
    w2 = cs.run_capital_sim(sigs, bars2, "partial_1r", "same_day", "worst", **kw)
    b2 = cs.run_capital_sim(sigs, bars2, "partial_1r", "same_day", "best", **kw)
    # worst: 10주 전량 9,900 → −1,000 − 210 = −1,210
    assert w2["metrics"]["total_pnl_krw"] == pytest.approx(-1210.0, abs=0.01)
    # best: 5주 10,100(+500) + 5주 9,900(−500) − 210 = −210
    assert b2["metrics"]["total_pnl_krw"] == pytest.approx(-210.0, abs=0.01)
    assert cs.ambiguous_pct(w2["trades"], b2["trades"]) == 100.0


# ⑥ 트레일링: +1R 도달 후 고점 갱신 → 고점 −1×ATR14 에서 청산
def test_trail_atr_exit_after_high_watermark():
    dates = [D0 + pd.Timedelta(days=i) for i in range(5)]
    # ATR14 = 100(1%) · 활성화 +1R = 10,100
    bars = _mk_bars("A", closes=[10000.0, 10150.0, 10350.0, 10300.0, 10300.0],
                    highs=[10090.0, 10200.0, 10400.0, 10400.0, 10400.0],
                    lows=[9990.0, 9950.0, 10250.0, 10280.0, 10200.0],
                    atrs=[100.0] * 5)
    sigs = [_sig(0, "A", dates[1])]
    kw = _kw(dates, hold_sessions=5)
    w = cs.run_capital_sim(sigs, bars, "trail_atr", "swing5", "worst", **kw)
    # 손계산(worst=비관적 순서): d1 고가 10,200 → 활성화, 상향 스탑 = max(9,900, 10,200−100)
    #   = 10,100. 저가 9,950 은 기존 스탑(9,900) 위지만 상향 스탑(10,100) 아래 → 비관 경로는
    #   10,100 청산. 수익 = 10×100 − 210 = 790 (ret +0.79%)
    assert w["metrics"]["total_pnl_krw"] == pytest.approx(790.0, abs=0.01)
    assert w["metrics"]["expectancy_pct"] == pytest.approx(0.79, abs=1e-4)
    assert w["metrics"]["n_trail_exits"] == 1
    assert w["trades"][0]["exits"] == [["trail", 10100.0, 10]]
    b = cs.run_capital_sim(sigs, bars, "trail_atr", "swing5", "best", **kw)
    # 손계산(best=낙관적 순서): d1 저가 9,950 > 기존 스탑 9,900 → 생존(스탑 10,100).
    #   d2 고가 10,400 → 스탑 10,300(저가 10,250 > 기존 스탑 10,100 → 생존).
    #   d3 저가 10,280 ≤ 기존 스탑 10,300 → 10,300 청산. 수익 = 10×300 − 210 = 2,790
    assert b["metrics"]["total_pnl_krw"] == pytest.approx(2790.0, abs=0.01)
    assert b["trades"][0]["exits"] == [["trail", 10300.0, 10]]
    assert cs.ambiguous_pct(w["trades"], b["trades"]) == 100.0  # 활성화일 순서가 갈림

    # 트레일 스탑은 초기 손절가 아래로 내려가지 않음: ATR 1,500 → 스탑 = max(9,900, 10,200−1,500)
    bars2 = _mk_bars("A", closes=[10000.0, 10150.0, 10100.0, 10100.0, 10100.0],
                     highs=[10090.0, 10200.0, 10150.0, 10150.0, 10150.0],
                     lows=[9990.0, 9950.0, 9890.0, 9890.0, 9890.0],
                     atrs=[1500.0] * 5)
    w2 = cs.run_capital_sim(sigs, bars2, "trail_atr", "swing5", "worst", **kw)
    # d1 활성화(고가 10,200) — 스탑 9,900 바닥 유지. d2 저가 9,890 ≤ 9,900 → 9,900 청산
    # 수익 = 10×(9,900−10,000) − 210 = −1,210 (ret −1.21%) — '손절 −1%' 계약 유지
    assert w2["metrics"]["total_pnl_krw"] == pytest.approx(-1210.0, abs=0.01)
    assert w2["trades"][0]["exits"] == [["trail", 9900.0, 10]]
    b2 = cs.run_capital_sim(sigs, bars2, "trail_atr", "swing5", "best", **kw)
    assert b2["metrics"]["total_pnl_krw"] == pytest.approx(-1210.0, abs=0.01)
    assert cs.ambiguous_pct(w2["trades"], b2["trades"]) == 0.0


# ⑦ MaxDD 계산(자본곡선) 손계산
def test_max_drawdown_hand_calculated():
    curve = [(D0 + pd.Timedelta(days=i)).date().isoformat() for i in range(4)]
    eqs = [100000.0, 101000.0, 99400.0, 103000.0]
    dd = cs.max_drawdown(list(zip(curve, eqs)))
    # 손계산: 피크 101,000(1/6) · 골 99,400(1/7) → MaxDD = 1,600/101,000×100 = 1.584158…%
    assert dd["maxdd_pct"] == pytest.approx(1600.0 / 101000.0 * 100.0, abs=1e-9)
    assert dd["peak"] == pytest.approx(101000.0)
    assert dd["trough"] == pytest.approx(99400.0)

    # 자본곡선 통합: fee 0 으로 두 거래(+2,900 / −1,300) → 피크 102,900 · 골 101,600
    dates = [D0 + pd.Timedelta(days=i) for i in range(4)]
    bars = {}
    bars.update(_mk_bars("A", closes=[10000.0, 10290.0, 10290.0, 10290.0],
                         highs=[10090.0, 10295.0, 10295.0, 10295.0],
                         lows=[9990.0, 10005.0, 10005.0, 10005.0]))
    bars.update(_mk_bars("B", closes=[10000.0, 10000.0, 9870.0, 9870.0],
                         highs=[10090.0, 10090.0, 10100.0, 10100.0],
                         lows=[9990.0, 9990.0, 9950.0, 9950.0]))
    sigs = [_sig(0, "A", dates[1]), _sig(1, "B", dates[2])]
    res = cs.run_capital_sim(sigs, bars, "fixed_1_3", "same_day", "worst",
                             **_kw(dates, fee_pct=0.0))
    m = res["metrics"]
    # A: 10주 @10,000 → 익절·손절 미터치(고가 10,295<10,300, 저가 10,005>9,900) → 종가 10,290
    #   +2,900 · B: 10주 @10,000 → 종가 9,870 → −1,300
    assert m["total_pnl_krw"] == pytest.approx(1600.0, abs=0.01)
    assert m["equity_end_krw"] == pytest.approx(101600.0, abs=0.01)
    assert m["maxdd_pct"] == pytest.approx(1300.0 / 102900.0 * 100.0, abs=1e-4)


# ⑧ 멱등 — 같은 입력 → 같은 바이트(실행 시각 미기록)
def test_idempotent_output_bytes():
    dates = [D0 + pd.Timedelta(days=i) for i in range(6)]
    bars = {}
    for code in "ABC":
        bars.update(_mk_bars(code, [10000.0 + i for i in range(6)]))
    sigs = [_sig(0, "A", dates[1]), _sig(1, "B", dates[1]), _sig(2, "C", dates[1]),
            _sig(3, "A", dates[2]), _sig(4, "B", dates[3]), _sig(5, "C", dates[3])]
    params = {"equity": 245435.0, "max_pos": 3, "per_stock_pct": 0.3334,
              "daily_max": 3, "price_min": 1000.0, "price_max": 50000.0,
              "slip_pct": 0.0, "fee_pct": 0.21, "hold_sessions": 5,
              "profiles": ["same_day", "swing5"]}
    cfg = {"json_out": "data/reports/capital_sim.json",
           "md_out": "data/reports/capital_sim.md"}
    r1 = cs.build_report(sigs, bars, dates, params, "2026-04-01", "2026-11-14",
                         cfg, n_loaded=100, raw_n=6)
    r2 = cs.build_report(sigs, bars, dates, params, "2026-04-01", "2026-11-14",
                         cfg, n_loaded=100, raw_n=6)
    b1 = json.dumps(r1, ensure_ascii=False, indent=2, sort_keys=True)
    b2 = json.dumps(r2, ensure_ascii=False, indent=2, sort_keys=True)
    assert b1 == b2                                   # 같은 입력 → 같은 바이트
    assert "generated_at" not in b1 and "run_at" not in b1   # 실행 시각 미기록
    assert len(r1["results"]) == 8                    # 4규칙 × 2프로파일
    assert "recommendation" in r1 and "walkforward" in r1
    assert r1["claim"]["runner"] == "capital_sim"
