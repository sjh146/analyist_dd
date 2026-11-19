"""trader_cycle 표본 정의 회귀 — `unfilled:` 마커 행은 청산 표본이 아니다.

WHY (2026-11-19 실측): 11-10 미체결 만료 주문(id 8)이 `exit_ts` 를 갖고 `pnl=0` 으로 종결 기록돼
있어, 청산 건수를 세는 모든 쿼리가 이 행을 **패배 거래**로 포함했다. 그 결과 매 틱 보고가
17건·승률 23.5%·기대값 -2,011원으로 나왔으나 실제 표본은 16건·25.0%·-2,136원이었다
(계기판 quant_scoreboard.py 는 이미 이 행을 제외해 -33,721원/16건으로 정확했다 — 같은 정의로 맞춘다).
"""
import importlib.util
import os
import sqlite3
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_cycle():
    path = os.path.join(_REPO, "scripts", "trader_cycle.py")
    spec = importlib.util.spec_from_file_location("trader_cycle_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fixture_journal(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """create table trades (
               id integer primary key, ts text, side text, code text, name text, qty real,
               price real, order_type text, order_resp_json text, entry_reason text,
               screener text, hwm_price real, algo_json text, stop_pct real, tp_pct real,
               kelly_f real, exit_ts text, exit_price real, exit_reason text, pnl real, fees real)"""
    )
    rows = [
        # 실청산 2건: +1000(승) / -400(패)
        ("2026-11-19T09:00:00+09:00", "buy", "111111", 10, 1000.0, "2026-11-19T09:30:00+09:00", 1100.0, "tick tp", 1000.0, 5.0),
        ("2026-11-19T09:05:00+09:00", "buy", "222222", 10, 1000.0, "2026-11-19T09:35:00+09:00", 960.0, "tick stop", -400.0, 5.0),
        # 마커: 체결 0 → 포지션 아니었음
        ("2026-11-19T09:10:00+09:00", "buy", "333333", 10, 1000.0, "2026-11-19T15:30:00+09:00", 1000.0,
         "unfilled: broker order 1 filled_qty=0 at close (expired), never a position - closed at entry, 0 PnL", 0.0, 0.0),
        # 미청산
        ("2026-11-19T10:00:00+09:00", "buy", "444444", 10, 1000.0, None, None, None, None, 0.0),
    ]
    for ts, side, code, qty, price, exit_ts, exit_price, reason, pnl, fees in rows:
        conn.execute(
            "insert into trades (ts, side, code, name, qty, price, exit_ts, exit_price, exit_reason, pnl, fees)"
            " values (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, side, code, code, qty, price, exit_ts, exit_price, reason, pnl, fees),
        )
    conn.commit()
    conn.close()


def test_unfilled_marker_excluded_from_closed_sample(tmp_path, monkeypatch):
    tc = _load_cycle()
    jp = tmp_path / "trade_journal.sqlite3"
    _fixture_journal(str(jp))
    monkeypatch.setattr(tc, "JOURNAL", str(jp))

    st = tc.measure()

    assert st["closed"] == 2, st            # unfilled 마커 제외
    assert st["win_rate"] == pytest.approx(0.5)
    assert st["expectancy_krw"] == pytest.approx(300.0)
    assert st["open_positions"] == 1
    assert st["today_closed"] == 2
    assert st["today_realized_krw"] == pytest.approx(600.0)
