#!/usr/bin/env python3
"""돈 계기판 계산층 회귀 테스트 — 실 DB 없이 픽스처로 검증한다.

계약 (scripts/money_scoreboard.py 모듈 docstring 의 수식 정의를 그대로 검사):
  1. 승/패 혼합: 승률 = 승수/(승수+패수), 평균이익/평균손실, 손익비 = Σ이익/|Σ손실|
  2. 수수료 합산: 총 수수료 = Σ fees, net = pnl − fees
  3. 보유 0/미체결 0: 브리지·저널·pg 어느 쪽도 비어 있으면 0 (None 이 아니다)
  4. 차단 후보 기회비용 합산: ret_i = (익일 시가/신호일 종가−1)×100, KRW = Σ ret×가정 크기
  5. 자본 사용률 피크: 진입~청산 구간 스윕으로 동시 보유 최대
  6. 주간·N일 창: exit 날짜 기준 분해
  7. 멱등: 같은 날짜 출력을 덮어써도 바이트가 같다 (재실행 안전)

데이터 접촉 금지: 이 파일은 DB·브리지·저널·네트워크를 절대 만지지 않는다.
"""
import importlib.util
import json
import os

SCOREBOARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "scripts", "money_scoreboard.py")


def _load():
    spec = importlib.util.spec_from_file_location("money_scoreboard_calc", SCOREBOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _trade(code, qty, price, pnl, fees=0.0, exit_ts="2026-09-30T09:26:48+09:00",
           ts="2026-09-29T15:03:40+09:00", screener="close", side="buy",
           exit_price=None):
    return {"id": code, "ts": ts, "side": side, "code": code, "name": code,
            "qty": qty, "price": price, "screener": screener,
            "exit_ts": exit_ts, "exit_price": exit_price if exit_price else price,
            "pnl": pnl, "fees": fees}


# ---------------------------------------------------------------------------
# 1) 승/패 혼합 승률 · 손익비
# ---------------------------------------------------------------------------

def test_win_loss_mixed_stats():
    trades = [
        _trade("A", 10, 1000, 500.0),     # 승
        _trade("B", 20, 1000, 800.0),     # 승
        _trade("C", 30, 1000, -900.0),    # 패
    ]
    stats = M.realized_stats(trades, window_days=30)["cum"]
    assert stats["closed"] == 3
    assert stats["wins"] == 2 and stats["losses"] == 1 and stats["flat"] == 0
    assert stats["pnl"] == 400.0                       # 500+800-900
    assert stats["win_rate"] == round(2 / 3, 4)        # 승률 = 승/(승+패)
    assert stats["avg_win"] == 650.0                   # (500+800)/2
    assert stats["avg_loss"] == 900.0                  # |−900|
    assert stats["profit_factor"] == round(1300 / 900, 4)  # Σ이익/|Σ손실|


def test_profit_factor_edge_cases():
    # 패가 없으면 손익비 정의 불가(측정 불가 = None), 승이 없으면 0
    only_win = [_trade("A", 10, 1000, 500.0)]
    assert M.realized_stats(only_win)["cum"]["profit_factor"] is None
    only_loss = [_trade("B", 10, 1000, -500.0)]
    assert M.realized_stats(only_loss)["cum"]["profit_factor"] == 0.0
    # 청산 0건이면 승률도 None
    no_close = [_trade("C", 10, 1000, None, exit_ts=None)]
    s = M.realized_stats(no_close)["cum"]
    assert s["closed"] == 0 and s["win_rate"] is None


# ---------------------------------------------------------------------------
# 2) 수수료 합산
# ---------------------------------------------------------------------------

def test_fees_summed():
    trades = [
        _trade("A", 10, 1000, 500.0, fees=100.0),
        _trade("B", 20, 1000, -300.0, fees=200.0),
        _trade("C", 30, 1000, -900.0, fees=0.0),
    ]
    stats = M.realized_stats(trades)["cum"]
    assert stats["fees"] == 300.0                     # 총 수수료 = Σ fees
    assert stats["net_pnl"] == round(-700.0 - 300.0, 2)  # net = pnl − fees
    assert stats["pnl"] == -700.0                     # pnl 자체는 수수료 미차감


def test_fees_unbooked_flag():
    # fees 전부 0 + 청산 존재 → 백필 경보 플래그 (실측 2026-10-02 상태 재현)
    trades = [_trade("A", 10, 1000, 500.0, fees=0.0)]
    stats = M.realized_stats(trades)["cum"]
    flag = bool(stats["closed"]) and stats["fees"] == 0.0
    assert flag is True  # build_report 의 fees_unbooked_flag 정의 그대로
    trades2 = [_trade("A", 10, 1000, 500.0, fees=50.0)]
    stats2 = M.realized_stats(trades2)["cum"]
    assert (bool(stats2["closed"]) and stats2["fees"] == 0.0) is False


# ---------------------------------------------------------------------------
# 3) 보유 0 / 미체결 0
# ---------------------------------------------------------------------------

def test_open_state_zero_holdings_zero_orders():
    bal = {"ok": True, "balance": {"positions_count": 0, "total_eval_pnl": 0}}
    pos = {"ok": True, "positions": []}
    orders = {"ok": True, "orders": []}
    out = M.open_state(bal, pos, orders, trades=[], pg_positions=0)
    assert out["positions_bridge"] == 0        # 0 이어야 한다(None 아님)
    assert out["pending_orders"] == 0          # 미체결 0
    assert out["unrealized_pnl"] == 0.0        # 미실현 0
    assert out["positions_journal_open"] == 0  # 저널 미청산 0
    assert out["positions_pg"] == 0            # pg 0


def test_open_state_falls_back_and_counts():
    # /balance 가 죽었어도 /positions 목록 길이로 센다
    out = M.open_state({"ok": False}, {"ok": True, "positions": [{}, {}]},
                       {"ok": True, "orders": [{}]}, trades=[_trade("A", 1, 1, None, exit_ts=None)],
                       pg_positions=None)
    assert out["positions_bridge"] == 2
    assert out["pending_orders"] == 1
    assert out["positions_journal_open"] == 1  # 미청산 저널 1건 교차검증


# ---------------------------------------------------------------------------
# 4) 차단 후보 기회비용 합산
# ---------------------------------------------------------------------------

def test_blocked_opportunity_sum():
    blocks = [
        {"code": "A", "gate": "daily_trade_count", "screener": "close"},
        {"code": "B", "gate": "balance_position_amount", "screener": "close"},
        {"code": "C", "gate": "balance_position_amount", "screener": "close"},
    ]
    price_map = {
        "A": {"close": 100.0, "next_open": 110.0},   # +10%
        "B": {"close": 200.0, "next_open": 190.0},   # −5%
        # C 는 가격 결측 → missing 으로 분리, 합산에서 제외
    }
    out = M.blocked_opportunity(blocks, price_map, notional=10000.0)
    assert out["n_blocks"] == 3 and out["n_measured"] == 2
    assert out["missing"] == ["C"]
    a = out["all"]
    assert a["sum_pct"] == round(10.0 - 5.0, 4)          # Σ ret
    assert a["avg_pct"] == round(2.5, 4)                 # 평균
    assert a["median_pct"] == 10.0                       # 중앙(정렬 [-5,10] → len//2 인덱스)
    assert a["up"] == 1 and a["down"] == 1
    assert a["krw_sum"] == round(1000.0 - 500.0, 2)      # 10000×(10%−5%)
    assert out["by_gate"]["daily_trade_count"]["krw_sum"] == 1000.0
    assert out["by_gate"]["balance_position_amount"]["krw_sum"] == -500.0


def test_blocked_opportunity_no_notional():
    # 체결이 없어 가정 크기가 없으면 % 는 나오고 KRW 는 '측정 불가'(None)
    blocks = [{"code": "A", "gate": "daily_trade_count"}]
    price_map = {"A": {"close": 100.0, "next_open": 105.0}}
    out = M.blocked_opportunity(blocks, price_map, notional=None)
    assert out["all"]["avg_pct"] == 5.0
    assert out["all"]["krw_sum"] is None
    assert M.assumed_notional([]) is None


# ---------------------------------------------------------------------------
# 5) 자본 사용률 피크 (동시 3종목 · 총 30%)
# ---------------------------------------------------------------------------

def test_utilization_peak_concurrent():
    trades = [
        _trade("A", 10, 1000, None, exit_ts="2026-09-30T09:00:00+09:00",
               ts="2026-09-29T15:00:00+09:00"),
        _trade("B", 20, 1000, None, exit_ts="2026-09-30T09:01:00+09:00",
               ts="2026-09-29T15:01:00+09:00"),
        _trade("C", 30, 1000, None, exit_ts="2026-09-30T09:02:00+09:00",
               ts="2026-09-29T15:02:00+09:00"),
    ]
    cu = M.capital_utilization(equity=200000.0, open_notional=0.0, trades=trades)
    assert cu["used_pct_now"] == 0.0                     # 현재 보유 0
    assert cu["peak"]["concurrent"] == 3                 # 동시 3종목 (한도 3)
    assert cu["peak"]["notional"] == 60000.0             # 10k+20k+30k
    assert cu["peak"]["pct_approx"] == 30.0              # 60000/200000 = 30% (한도)
    assert cu["limits"] == {"max_daily_trades": 3, "per_stock_pct": 0.10,
                            "max_open_positions": 3, "total_exposure_pct": 0.30}


def test_utilization_equity_unknown():
    cu = M.capital_utilization(equity=None, open_notional=0.0, trades=[])
    assert cu["equity"] is None and cu["used_pct_now"] is None


# ---------------------------------------------------------------------------
# 6) 주간 · 최근 N일 창
# ---------------------------------------------------------------------------

def test_weekly_and_recent_window():
    trades = [
        _trade("A", 10, 1000, 100.0, exit_ts="2026-09-24T09:00:00+09:00"),  # 목 (W39)
        _trade("B", 10, 1000, 200.0, exit_ts="2026-09-30T09:00:00+09:00"),  # 수 (W40)
        _trade("C", 10, 1000, -50.0, exit_ts="2026-10-01T09:00:00+09:00"),  # 목 (W40)
    ]
    now = __import__("datetime").date(2026, 10, 2)
    rep = M.realized_stats(trades, now=now, window_days=30)
    weeks = {w["week_start"]: w for w in rep["weekly"]}
    assert weeks["2026-09-21"]["pnl"] == 100.0
    assert weeks["2026-09-28"]["pnl"] == 150.0 and weeks["2026-09-28"]["closed"] == 2
    assert rep["recent_30d"]["pnl"] == 250.0             # 3건 모두 30일 창 안
    rep7 = M.realized_stats(trades, now=now, window_days=7)
    assert rep7["recent_7d"]["pnl"] == 150.0             # 09-24 건은 7일 창 밖(경계 09-25)


# ---------------------------------------------------------------------------
# 7) 픽스처 모드 종단 + 멱등 (재실행 시 같은 날짜 파일을 덮어쓴다)
# ---------------------------------------------------------------------------

def _write_fixture(tmp_path):
    fdir = os.path.join(str(tmp_path), "fixture")
    os.makedirs(fdir)
    trades = [
        _trade("206560", 31, 973.9678, -898.0, ts="2026-09-29T15:03:40+09:00",
               exit_price=945.0),
        _trade("105740", 3, 9271.3333, 416.0, ts="2026-09-29T15:03:41+09:00",
               exit_price=9410.0),
    ]
    decisions = [
        {"ts": "2026-09-29T15:03:42+09:00", "screener": "close", "code": "396300",
         "name": "HT로보틱스", "score": 90.0,
         "fails": ["balance_position_amount", "daily_trade_count"]},
        {"ts": "2026-09-29T15:03:43+09:00", "screener": "close", "code": "131100",
         "name": "티엔엔터테인먼트", "score": 90.0, "fails": ["daily_trade_count"]},
    ]
    with open(os.path.join(fdir, "journal.json"), "w", encoding="utf-8") as f:
        json.dump({"trades": trades, "decisions": decisions}, f, ensure_ascii=False)
    with open(os.path.join(fdir, "pg.json"), "w", encoding="utf-8") as f:
        json.dump({"trader_fills": [], "positions_rows": 0,
                   "market_data": [
                       {"code": "396300", "date": "2026-09-29", "open": 100.0, "close": 100.0},
                       {"code": "396300", "date": "2026-09-30", "open": 110.0, "close": 108.0},
                       {"code": "131100", "date": "2026-09-29", "open": 100.0, "close": 100.0},
                       {"code": "131100", "date": "2026-09-30", "open": 90.0, "close": 91.0},
                       # 집행 종목 2건 (판정 벤치마크용 시가)
                       {"code": "206560", "date": "2026-09-29", "open": 980.0, "close": 949.0},
                       {"code": "206560", "date": "2026-09-30", "open": 949.0, "close": 927.0},
                       {"code": "105740", "date": "2026-09-29", "open": 9620.0, "close": 9300.0},
                       {"code": "105740", "date": "2026-09-30", "open": 9420.0, "close": 9620.0},
                   ]}, f, ensure_ascii=False)
    with open(os.path.join(fdir, "bridge.json"), "w", encoding="utf-8") as f:
        json.dump({"health": {"ok": True},
                   "balance": {"ok": True, "balance": {"equity": 100000.0,
                                                       "positions_count": 0,
                                                       "total_eval_pnl": 0,
                                                       "total_buy_amount": 0}},
                   "positions": {"ok": True, "positions": []},
                   "orders": {"ok": True, "orders": []}}, f, ensure_ascii=False)
    for name in ("close_path_gap_history.jsonl", "trader_ledger.jsonl"):
        open(os.path.join(fdir, name), "w", encoding="utf-8").close()
    for name, payload in (
        ("gate_probe_summary.json", {"days": {"total": 87, "gate_open": 0, "r1_ok": 38,
                                              "heat_ok": 3}}),
        ("blocked_candidates.json", {"n": 20, "avg_ret_pct": 1.97}),
        ("audit_path.json", {"info": {"last_cycle": {"reasons": ["HEAT block close: avg +17.0% > +15.0%"],
                                                     "blocked_screeners": 1}}}),
        ("krx_holidays.json", ["2026-10-03", "2026-10-05"]),
        ("loop_state.json", {"daily": {"start_equity": 100000.0, "closed_pnl": 0.0}}),
    ):
        with open(os.path.join(fdir, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    return fdir


def test_fixture_mode_end_to_end(tmp_path):
    fdir = _write_fixture(tmp_path)
    rep = M.build_report({"mode": "fixture", "days": 30, "fixture_dir": fdir,
                          "out_dir": str(tmp_path), "repo": "fixture"})
    assert rep["realized"]["cum"]["pnl"] == -482.0            # -898+416
    assert rep["realized"]["cum"]["closed"] == 2
    assert rep["realized"]["fees_unbooked_flag"] is True      # fees=0 청산 존재 → 백필 경보
    assert rep["open_state"]["positions_bridge"] == 0         # 보유 0
    assert rep["open_state"]["pending_orders"] == 0           # 미체결 0
    assert rep["open_state"]["unrealized_pnl"] == 0.0         # 미실현 0
    # 차단 2종목: +10% / −10% (가정 크기 = 실제 체결 평균, KRW 합은 +/− 상쇄 → 0)
    occ = rep["opportunity_cost"]["blocked_2026-09-29"]["close"]
    assert occ["n_blocks"] == 2 and occ["n_measured"] == 2
    assert occ["all"]["sum_pct"] == 0.0
    assert occ["all"]["krw_sum"] == round(0.10 * occ["assumed_notional_krw"]
                                          - 0.10 * occ["assumed_notional_krw"], 2)
    assert rep["verdict"]["top1"]["id"] == "exit_window_miss"  # 체결가 vs 시가 실측 존재
    # 206560: exit 945 vs 시가 949 → −124 · 105740: exit 9410 vs 9420 → −30
    assert rep["verdict"]["top1"]["krw"] == -154.0


def test_output_idempotent(tmp_path):
    """재실행 안전: 같은 날짜 파일을 덮어써도 내용이 같다."""
    fdir = _write_fixture(tmp_path)
    out_dir = os.path.join(str(tmp_path), "out")
    cfg = {"mode": "fixture", "days": 30, "fixture_dir": fdir,
           "out_dir": out_dir, "repo": "fixture"}

    def run_once():
        rep = M.build_report(cfg)
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.join(out_dir, "scoreboard_%s" % rep["date"])
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write(M.render_md(rep))
        return base

    b1 = run_once()
    first = (open(b1 + ".json", encoding="utf-8").read(),
             open(b1 + ".md", encoding="utf-8").read())
    b2 = run_once()
    second = (open(b2 + ".json", encoding="utf-8").read(),
              open(b2 + ".md", encoding="utf-8").read())
    assert b1 == b2
    assert first == second
