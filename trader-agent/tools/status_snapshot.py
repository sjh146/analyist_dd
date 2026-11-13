"""Operator status snapshot: journal vs broker, exit lines, working orders.

One read-only command that answers "where do we stand right now?":
broker equity/cash, today's realised P&L from the journal, every open position
with its own exit line and distance to it, journal/broker mismatches, working
orders, and whether the day's low has touched any exit line (a cheap breach
check that works even while the loop is stopped).

    C:\\Users\\jhshi\\Python312-64\\python.exe tools\\status_snapshot.py

Read-only: three GETs to the bridge (/balance, /positions, /orders, /quotes)
and a SELECT on the journal.  Never place this on the loop's cadence.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trader_core.net import urlopen as local_urlopen  # noqa: E402

BRIDGE = os.environ.get("TRADER_BRIDGE_URL", "http://127.0.0.1:8100")
JOURNAL = os.environ.get("TRADER_JOURNAL", "journal/trade_journal.sqlite3")
try:  # trailing levels live in the engine's Config -- never hardcode them here
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from trader_core.config import Config as _Config

    _cfg = _Config()
    ARM_PCT = float(_cfg.trailing_arm_pct)       # Config.trailing_arm_pct
    TRAIL_PCT = float(_cfg.trailing_stop_pct)    # Config.trailing_stop_pct
except Exception:  # noqa: BLE001 - keep the tool runnable outside the repo root
    ARM_PCT = 0.055
    TRAIL_PCT = 0.05


def get(path: str) -> dict:
    with local_urlopen(BRIDGE + path, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def line_for(row: dict) -> tuple:
    """Return ``(exit_line, armed)`` for a journal row."""
    entry = float(row["price"])
    stop = float(row["stop_pct"] or 0)
    hwm = float(row["hwm_price"] or entry)
    line = entry * (1 - stop)
    armed = hwm >= entry * (1 + ARM_PCT)
    if armed:
        line = max(line, hwm * (1 - TRAIL_PCT))
    return line, armed


def main() -> int:
    try:
        bal = get("/balance").get("balance", {})
        positions = get("/positions").get("positions", [])
        orders = get("/orders").get("orders", [])
    except Exception as exc:  # noqa: BLE001 - operator tool, report and stop
        print("bridge unavailable ({0}) -- loop state and journal are below".format(
            str(exc)[:120]))
        bal, positions, orders = {}, [], []
    pos = {p["code"]: p for p in positions}

    con = sqlite3.connect(JOURNAL)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT id,ts,code,name,qty,price,stop_pct,hwm_price,screener FROM trades "
        "WHERE exit_ts IS NULL ORDER BY id")]
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d")
    closed = [dict(r) for r in con.execute(
        "SELECT code,name,qty,price,exit_price,exit_reason,pnl,exit_ts FROM trades "
        "WHERE exit_ts >= ? ORDER BY id", (today,))]

    print("=== BROKER ===")
    if bal:
        print("equity {0:,.0f}  cash {1:,.0f}  stock_eval {2:,.0f}  "
              "cost {3:,.0f}  positions {4}".format(
                  float(bal.get("equity") or 0), float(bal.get("cash") or 0),
                  float(bal.get("total_eval_amount") or 0),
                  float(bal.get("total_buy_amount") or 0),
                  bal.get("positions_count")))
    else:
        print("unavailable (bridge down)")

    print("=== CLOSED TODAY (journal) ===")
    realised = 0.0
    for r in closed:
        realised += float(r["pnl"] or 0)
        print("  {0} {1} qty {2} {3:,.0f}->{4:,.0f} pnl {5:+,.0f} [{6}] {7}".format(
            r["code"], r["name"], r["qty"], float(r["price"]),
            float(r["exit_price"] or 0), float(r["pnl"] or 0),
            r["exit_reason"], str(r["exit_ts"] or "")[11:19]))
    print("  realised total (journal, quote-priced) = {:+,.0f}".format(realised))

    print("=== OPEN POSITIONS vs EXIT LINE (closest to its exit first) ===")

    def distance_pct(row: dict):
        """Distance from the mark down to the row's own exit line, in %."""
        p = pos.get(row["code"])
        if not p or not float(p.get("qty") or 0):
            return 9_999.0
        mark = float(p["eval_amount"]) / float(p["qty"])
        line, _ = line_for(row)
        return (mark - line) / mark * 100 if mark else 9_999.0

    for r in sorted(rows, key=distance_pct):
        code = r["code"]
        entry = float(r["price"])
        line, armed = line_for(r)
        p = pos.get(code)
        if p is None:
            print("  {0} {1} qty {2} entry {3:,.0f} :: NO BROKER POSITION".format(
                code, r["name"], r["qty"], entry))
            continue
        mark = float(p["eval_amount"]) / float(p["qty"] or 1)
        print("  {0} {1:<16} qty {2:>3} entry {3:>9,.0f} mark {4:>9,.0f} "
              "({5:+6.2f}%) line {6:>9,.0f} to_exit {7:+6.2f}%{8}".format(
                  code, str(r["name"])[:16], p["qty"], entry, mark,
                  (mark / entry - 1) * 100, line,
                  (mark - line) / mark * 100 if mark else 0.0,
                  " [trail]" if armed else ""))

    orphan = [c for c in pos if c not in {r["code"] for r in rows}]
    print("=== BROKER POSITION WITHOUT A JOURNAL ROW ===")
    print("  " + (", ".join(orphan) if orphan else "none"))
    print("=== WORKING ORDERS ===")
    for o in orders:
        print("  order_no {0} {1} qty {2} price {3} filled {4} cancelable {5}".format(
            o.get("order_no"), o.get("code"), o.get("qty"), o.get("price"),
            o.get("filled_qty"), o.get("cancelable_qty")))
    if not orders:
        print("  none")

    codes = [r["code"] for r in rows if r["code"] in pos]
    if codes and bal:
        try:
            quotes = get("/quotes?codes=" + ",".join(codes)).get("quotes", {})
        except Exception:  # noqa: BLE001
            quotes = {}
        print("=== TODAY'S LOW vs EXIT LINE (breach check) ===")
        for r in rows:
            code = r["code"]
            quote = quotes.get(code) or {}
            if code not in pos or not quote:
                continue
            line, _ = line_for(r)
            low = float(quote.get("low") or 0)
            print("  {0} line {1:>9,.0f} day_low {2:>9,.0f} {3} now {4:>9,.0f}".format(
                code, line, low,
                "BREACHED" if low and low <= line else "ok",
                float(quote.get("current") or 0)))
        print("  note: the day's low may predate the entry; tools/gap_audit.py "
              "splits the day into windows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
