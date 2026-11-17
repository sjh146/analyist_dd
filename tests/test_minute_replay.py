"""docs/spec_minute_replay.md '테스트' 절 계약 6개 — DB 없이 순수 합성 데이터로 검증.

① 같은 분 양방향 → 손절 우선(퇴화 규칙으로 직접 호출 + 정규 규칙의 구조적 불가능 확인)
② 이른 분 익절 → 익절가(그 분 종가) 체결    ③ 미도달 → 당일 종가(eod) 청산
④ 분봉 없음 → skipped_no_minute(일봉 대체 없음)  ⑤ 수량·비용 계산이 capital_sim 과 같은 값
   (24,200원 → 3주 · 왕복 0.21%×72,600 = 152.46 — 스펙 인용 실측 경계 1건 대조)
⑥ 멱등(같은 입력 → 같은 바이트 · 실행 시각 미기록)

모든 테스트는 합성 분봉 종가·자본만 사용한다(DB 접속 없음 — 재생 엔진 순수 함수 계약).
분봉 종가는 정규장(09:00~15:30) 오름차순 리스트로 넘긴다(스펙 §2 경로 규칙).
"""
import json
import os
import sys

import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import capital_sim as cs  # noqa: E402
import minute_replay as mr  # noqa: E402

D0 = pd.Timestamp("2026-01-05")


def _mk_bars(code, closes, d0=D0):
    """합성 종목 봉(재생은 시가·종가·atr 만 참조 — 고/저는 일봉 밴드 대조용)."""
    n = len(closes)
    dates = [d0 + pd.Timedelta(days=i) for i in range(n)]
    return {code: {"dates": pd.Series(dates).to_numpy(dtype="datetime64[ns]"),
                   "open": pd.Series([float(c) for c in closes]).to_numpy(dtype=float),
                   "high": pd.Series([c + 100.0 for c in closes]).to_numpy(dtype=float),
                   "low": pd.Series([c - 99.0 for c in closes]).to_numpy(dtype=float),
                   "close": pd.Series([float(c) for c in closes]).to_numpy(dtype=float),
                   "atr14": pd.Series([100.0] * n).to_numpy(dtype=float)}}


def _sig(idx, code, entry_ts, px=10000.0, score=5):
    return {"sig_idx": idx, "stock": code,
            "sig_date": entry_ts - pd.Timedelta(days=1),
            "entry_date": entry_ts, "entry_open": float(px), "edge_score": score}


def _kw(dates, **over):
    kw = dict(equity0=245435.0, max_pos=3, per_stock_pct=0.3334, daily_max=3,
              price_min=1000.0, price_max=50000.0, dates=dates)
    kw.update(over)
    return kw


# ① 같은 분 양방향 → 손절 우선(스펙 §2). 종가만 저장하는 계약에서는 손절<익절이라 구조적으로
#    불가능하므로, 임계가 겹치는 퇴화 규칙으로 walk_day 를 직접 호출해 우선순위 계약을 검증하고,
#    정규 규칙에서는 ambiguous 가 나올 수 없음을 구조적 사실로 함께 확인한다.
def test_same_minute_both_cross_stop_priority():
    # 퇴화 규칙: 손절 0% · 익절 0% → 두 임계 모두 진입가. 첫 분 종가 = 진입가 → 동시 교차.
    res = mr.walk_day([10000.0, 10100.0], 10000.0,
                      {"kind": "fixed", "stop_pct": 0.0, "tp_pct": 0.0})
    assert res[0] == "stop"          # 보수적으로 손절 우선
    assert res[3] is True            # 애매동일분 계수
    assert res[1] == 10000.0 and res[2] == 0   # 그 분 종가로 체결
    # 구조적 사실: 정규 규칙은 손절<진입<익절 → 단일 분봉 종가가 두 임계를 동시에 넘을 수 없다
    res2 = mr.walk_day([9900.0, 10300.0], 10000.0,
                       {"kind": "fixed", "stop_pct": 1.0, "tp_pct": 3.0})
    assert res2[0] == "stop" and res2[3] is False   # 손절 먼저 터치, 애매 아님


# ② 이른 분 익절 → 처음 임계를 넘은 그 분 종가로 익절 체결
def test_early_minute_target_fill():
    dates = [D0 + pd.Timedelta(days=i) for i in range(3)]
    bars = _mk_bars("A", [10000.0] * 3)
    sigs = [_sig(0, "A", dates[1])]
    minute = {("A", dates[1].date().isoformat()): [10000.0, 10050.0, 10300.0]}
    res = mr.run_minute_replay(sigs, bars, minute, "fixed_1_3", "same_day",
                               **_kw(dates))
    m = res["metrics"]
    # 손계산: 예산 min(245,435×0.3334, 245,435/3)=81,811.67 → floor(81,811.67/10,000)=8주.
    #   3번째 분(인덱스 2) 종가 10,300 ≥ 익절 10,300 → 8주 @10,300.
    #   수익 = 8×300 = 2,400 − 왕복 0.21%×80,000 = 168 → +2,232원(ret +2.79%)
    assert m["trades"] == 1 and m["n_target"] == 1 and m["n_stop"] == 0
    t = res["trades"][0]
    assert t["exits"] == [["target", 10300.0, 8]]
    assert t["hold_minutes"] == 3          # 진입일 09:00 부터 3분 만에 청산
    assert t["ambiguous_same_minute"] is False
    assert t["pnl_krw"] == pytest.approx(2232.0, abs=0.01)
    assert m["expectancy_pct"] == pytest.approx(2.79, abs=1e-4)


# ③ 15:30 까지 미도달 → 당일 종가(=마지막 정규장 분봉 종가) 청산
def test_no_touch_eod_close():
    dates = [D0 + pd.Timedelta(days=i) for i in range(3)]
    bars = _mk_bars("A", [10000.0] * 3)
    sigs = [_sig(0, "A", dates[1])]
    minute = {("A", dates[1].date().isoformat()): [10000.0, 10020.0, 10010.0, 10080.0]}
    res = mr.run_minute_replay(sigs, bars, minute, "fixed_1_3", "same_day",
                               **_kw(dates))
    m = res["metrics"]
    # 손계산: 손절 9,900 · 익절 10,300 모두 미터치 → eod @10,080(마지막 분봉).
    #   8주 × +80 = 640 − 168 = +472원 (ret +0.59%)
    assert m["trades"] == 1 and m["n_eod"] == 1
    t = res["trades"][0]
    assert t["exits"] == [["eod", 10080.0, 8]]
    assert t["hold_minutes"] == 4          # 전 분(4분) 보유 후 종가 청산
    assert t["pnl_krw"] == pytest.approx(472.0, abs=0.01)
    assert m["expectancy_pct"] == pytest.approx(0.59, abs=1e-4)


# ④ 분봉 없는 (종목,일) → 건너뛰고 skipped_no_minute 로 센다(일봉 대체 금지 — 스펙)
def test_missing_minute_skipped_no_minute():
    dates = [D0 + pd.Timedelta(days=i) for i in range(3)]
    bars = _mk_bars("A", [10000.0] * 3)
    sigs = [_sig(0, "A", dates[1])]
    res = mr.run_minute_replay(sigs, bars, {}, "fixed_1_3", "same_day", **_kw(dates))
    m = res["metrics"]
    assert m["trades"] == 0
    assert m["skipped_no_minute"] == 1     # 자본·가격대 등 다른 사유 아님
    assert m["skipped_capital"] == 0 and m["skipped_price_band"] == 0


# ⑤ 수량·비용 계산이 capital_sim 과 같은 값 — 스펙 인용 실측 경계 1건 대조
def test_qty_fee_boundary_matches_capital_sim():
    # 실측(market_data, 2026-11-18): 096530 2026-11-16 종가 24,200원 →
    #   floor(81,811.67/24,200) = 3주 · 왕복 0.21%×72,600 = 152.46원
    dates = [D0 + pd.Timedelta(days=i) for i in range(3)]
    bars = _mk_bars("A", [24200.0] * 3)
    sigs = [_sig(0, "A", dates[1], px=24200.0)]
    minute = {("A", dates[1].date().isoformat()): [24200.0] * 5}   # 전 분 미터치 → eod
    res = mr.run_minute_replay(sigs, bars, minute, "fixed_1_3", "same_day",
                               **_kw(dates))
    t = res["trades"][0]
    assert t["exits"] == [["eod", 24200.0, 3]]     # 수량 3주 — cs.entry_budget/size_qty 재사용
    assert t["pnl_krw"] == pytest.approx(-152.46, abs=0.01)
    assert t["ret_pct"] == pytest.approx(-0.21, abs=1e-9)
    # 같은 신호·같은 봉으로 capital_sim 도 동일값(경계 1건 대조 — import 재사용 증명)
    ref = cs.run_capital_sim(sigs, bars, "fixed_1_3", "same_day", "worst", **_kw(dates))
    assert ref["trades"][0]["exits"] == [["eod", 24200.0, 3]]
    assert ref["metrics"]["total_pnl_krw"] == pytest.approx(-152.46, abs=0.01)


# ⑥ 멱등 — 같은 입력 → 같은 바이트(실행 시각 미기록)
def test_idempotent_report_bytes():
    dates = [D0 + pd.Timedelta(days=i) for i in range(6)]
    bars, minute = {}, {}
    for code in "ABC":
        bars.update(_mk_bars(code, [10000.0 + i for i in range(6)]))
        minute[(code, dates[1].date().isoformat())] = [10000.0, 10050.0, 10300.0]
    sigs = [_sig(0, "A", dates[1]), _sig(1, "B", dates[1]), _sig(2, "C", dates[2])]
    kw = dict(equity0=245435.0, max_pos=3, per_stock_pct=0.3334, daily_max=3,
              price_min=1000.0, price_max=50000.0, slip_pct=0.0, fee_pct=cs.FEE_PCT,
              hold_sessions=5)
    params = {"equity": 245435.0, "max_pos": 3, "per_stock_pct": 0.3334,
              "daily_max": 3, "price_min": 1000.0, "price_max": 50000.0,
              "fee_pct": cs.FEE_PCT, "hold_sessions": 5, "profile": "same_day",
              "daily_profile": "same_day", "rules": ["fixed_1_3"], "engine_kw": kw}
    cfg = {"json_out": "data/reports/minute_replay.json",
           "md_out": "data/reports/minute_replay.md"}
    stats = {"rows": 3, "paths": 3, "stocks": 3, "days": 1,
             "min_date": dates[1].date().isoformat(),
             "max_date": dates[1].date().isoformat()}
    r1 = mr.build_report(sigs, bars, dates, minute, params, "2026-01-05", "2026-01-10",
                         cfg, n_loaded=100, raw_n=3, minute_stats=stats)
    r2 = mr.build_report(sigs, bars, dates, minute, params, "2026-01-05", "2026-01-10",
                         cfg, n_loaded=100, raw_n=3, minute_stats=stats)
    b1 = json.dumps(r1, ensure_ascii=False, indent=2, sort_keys=True)
    b2 = json.dumps(r2, ensure_ascii=False, indent=2, sort_keys=True)
    assert b1 == b2                                   # 같은 입력 → 같은 바이트
    assert "generated_at" not in b1 and "run_at" not in b1   # 실행 시각 미기록
    assert len(r1["results"]) == 1 and len(r1["results"][0]["segments"]) == 3  # 전/앞/뒤
    assert r1["claim"]["runner"] == "minute_replay"
    seg = r1["results"][0]["segments"][0]
    assert "daily_worst_ev" in seg and "daily_best_ev" in seg   # 일봉 밴드 병기
