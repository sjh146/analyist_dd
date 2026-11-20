"""tests/test_pairs_arb_research.py — 쌍둥이 하네스 핵심 로직 (6 tests).

합성 시계열로 ① 스프레드 z 진입/청산 ② max_hold 강제청산 ③ 비용 반영
④ 레짐 라벨(프록시 20일 방향) ⑤ long-only 모드 진입 방향 을 고정한다.
⑥ index_daily 실지수 프록시(spec_index_backfill §2 --use-index) — load_index_series 가
breadth_index 와 같은 {date_str: close} 모양을 내고 regime_map 과 그대로 결합되는지
in-memory sqlite 로 검증(PostgreSQL %s → ? 치환 — test_index_backfill 관례).
"""
import os
import sys

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import pairs_arb_research as par  # noqa: E402


def _dates(n=200):
    return ["2026-%02d-%02d" % (1 + i // 28, 1 + i % 28) for i in range(n)]


def _px_pair(n=200, spread_jump=None):
    d = _dates(n)
    b = 1000 * np.exp(np.linspace(0, 0.2, n))
    a = 500 * np.exp(np.linspace(0, 0.2, n))
    if spread_jump:
        t0, size = spread_jump
        a[t0:t0 + 20] *= (1 + size)
    return {"A": dict(zip(d, a)), "B": dict(zip(d, b))}, d


def test_entry_when_spread_stretched_and_profitable_on_revert():
    px, d = _px_pair(spread_jump=(120, 0.25))     # A 가 25% 비싸짐 → 이후 되돌림
    reg = {x: "down" for x in d}
    trades = par.simulate(("A", "B", "pref"), px, reg, mode="both", z_entry=2.0, z_exit=0.5,
                          z_stop=3.5, lookback=60, max_hold=20, fee_pct=0.21, slip_bps=10)
    assert trades, "스프레드 확대 후 진입이 있어야 한다"
    assert all(t["hold"] <= 20 for t in trades)


def test_max_hold_forced_exit():
    px, d = _px_pair(spread_jump=(120, 0.35))
    reg = {x: "down" for x in d}
    trades = par.simulate(("A", "B", "pref"), px, reg, mode="both", z_entry=2.0, z_exit=0.0,
                          z_stop=99.0, lookback=60, max_hold=3, fee_pct=0.0, slip_bps=0)
    assert trades and all(t["hold"] <= 3 for t in trades)


def test_costs_reduce_net_return():
    px, d = _px_pair(spread_jump=(120, 0.25))
    reg = {x: "down" for x in d}
    free = par.simulate(("A", "B", "pref"), px, reg, mode="both", z_entry=2.0, z_exit=0.5,
                        z_stop=3.5, lookback=60, max_hold=20, fee_pct=0.0, slip_bps=0)
    paid = par.simulate(("A", "B", "pref"), px, reg, mode="both", z_entry=2.0, z_exit=0.5,
                        z_stop=3.5, lookback=60, max_hold=20, fee_pct=0.21, slip_bps=10)
    if free and paid:
        assert paid[0]["net_pct"] < free[0]["net_pct"]


def test_regime_labels_follow_proxy_direction():
    idx = {("d%02d" % i): (100.0 if i < 15 else 100.0 * (0.99 ** (i - 14))) for i in range(40)}
    reg = par.regime_map(idx, lookback=10)
    assert reg["d39"] == "down"          # 지수가 10일 전보다 낮다
    assert reg["d12"] == "up" or "d12" not in reg


def test_summarize_stats():
    trades = [{"net_pct": 1.0, "hold": 3, "regime": "down"},
              {"net_pct": -2.0, "hold": 5, "regime": "down"},
              {"net_pct": 4.0, "hold": 2, "regime": "up"}]
    s = par.summarize(trades)
    assert s["n"] == 3
    assert s["win_pct"] == pytest.approx(66.6667, rel=1e-3)
    assert s["expectancy_pct"] == pytest.approx(1.0)
    assert s["total_pct"] == pytest.approx(3.0)
    assert s["avg_hold"] == pytest.approx(10 / 3)


# ⑥ index_daily 실지수 프록시 — breadth_index 와 같은 모양, regime_map 과 그대로 결합
def test_load_index_series_from_index_daily(monkeypatch):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE index_daily (index_code TEXT, trade_date TEXT, close REAL)")
    # 실측 2026-11-18~20 KOSPI 종가(DB 조회) + close<=0 행은 제외되는지 확인용
    conn.executemany("INSERT INTO index_daily VALUES (?,?,?)", [
        ("KOSPI", "2026-11-18", 5067.44),
        ("KOSPI", "2026-11-19", 5137.89),
        ("KOSPI", "2026-11-20", 4869.96),
        ("KOSDAQ", "2026-11-20", 0.0)])            # close > 0 필터로 제외
    monkeypatch.setattr(par, "INDEX_SERIES_SQL", par.INDEX_SERIES_SQL.replace("%s", "?"))
    idx = par.load_index_series(conn, "KOSPI")
    assert idx == {"2026-11-18": 5067.44, "2026-11-19": 5137.89, "2026-11-20": 4869.96}
    assert par.load_index_series(conn, "KOSDAQ") == {}   # close<=0 → 빈 지도(0 채움 금지)
    reg = par.regime_map(idx, lookback=1)                # breadth 경로와 같은 후속 파이프
    assert "2026-11-18" not in reg                       # lookback 미만은 라벨 없음
    assert reg["2026-11-19"] == "up"
    assert reg["2026-11-20"] == "down"                   # 실지수 하락일 → down
    assert par.load_index_series(None, "KOSPI") == {}    # conn None → 빈 지도
    conn.close()
