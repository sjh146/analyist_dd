"""계기판 표본 정의 회귀 — `unfilled` 문구가 접두어가 아니어도 청산 표본에서 빠져야 한다.

WHY (2026-11-27 실측): 트레이더는 미체결 진입 주문을 `exit_reason='entry order cancelled
unfilled (74s)'` 로 종결 기록한다. quant_scoreboard 는 `NOT LIKE 'unfilled:%'` 접두어
검사만 했기 때문에 이 행(id 38, 0원)을 **패배 거래**로 셌다 → 39건/승률 33.3%/-885원
(실제 38건/34.2%/-908원). trader_cycle 은 T65 에서 `instr(lower(...),'unfilled')=0` 로
고쳤지만 계기판은 남아 있었다 — 두 도구가 같은 표본 정의를 쓰도록 고정한다.

표본 정의(2026-11-27 수리): 청산 표본은 `exit_reason` 에 `unfilled` 가 **부분 문자열로** 들어간 행을
제외한다. 옛 구현은 `NOT LIKE 'unfilled:%'` 접두어 검사라 오늘 실제 문구
`entry order cancelled unfilled (74s)` (id 38, 0원)을 패배 거래로 셌고, 계기판이 39건/33.3%/-885원을
보고했다(실제 38건/34.2%/-910원). trader_cycle 은 T65 에서 같은 정의로 고쳐져 있었다.
"""
import importlib.util
import os
import sqlite3
import sys
from datetime import date

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_scoreboard(tmp_journal):
    path = os.path.join(_REPO, "scripts", "quant_scoreboard.py")
    spec = importlib.util.spec_from_file_location("quant_scoreboard_sample_def", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.JOURNAL = str(tmp_journal)
    mod._copy_journal_local = lambda: ("", str(tmp_journal))
    return mod


def _fixture(path):
    today = date.today().isoformat()
    conn = sqlite3.connect(path)
    conn.execute(
        """create table trades (
               id integer primary key, ts text, side text, code text, name text, qty real,
               price real, order_type text, order_resp_json text, entry_reason text,
               screener text, hwm_price real, algo_json text, stop_pct real, tp_pct real,
               kelly_f real, exit_ts text, exit_price real, exit_reason text, pnl real, fees real)"""
    )
    rows = [
        ("buy", "111111", 10, 1000.0, 1100.0, "tick tp", 1000.0),
        ("buy", "222222", 10, 1000.0, 960.0, "tick stop", -400.0),
        ("buy", "555555", 1, 25300.0, 25300.0, "entry order cancelled unfilled (74s)", 0.0),
        ("buy", "333333", 10, 1000.0, 1000.0, "unfilled: broker order expired", 0.0),
    ]
    for side, code, qty, px, ex, reason, pnl in rows:
        conn.execute(
            "insert into trades (ts, side, code, name, qty, price, exit_ts, exit_price, exit_reason, pnl, fees)"
            " values (?,?,?,?,?,?,?,?,?,?,?)",
            (today + "T09:00:00+09:00", side, code, code, qty, px,
             today + "T09:30:00+09:00", ex, reason, pnl, 0.0),
        )
    conn.commit()
    conn.close()


def test_unfilled_rows_do_not_count_as_trades(tmp_path):
    db = tmp_path / "trade_journal.sqlite3"
    _fixture(db)
    mod = _load_scoreboard(db)
    st = mod.trader_stanza()
    assert st["error"] is None, st
    assert st["trades"] == 2, st
    assert st["wins"] == 1, st
    assert st["win_rate"] == 50.0, st
    assert st["realized_krw"] == 600, st        
    assert st["expectancy_krw"] == 300, st      
