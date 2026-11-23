"""Status snapshot: journal vs broker, real free slots, distance to each exit line.

Run with the Windows 64-bit interpreter, because the journal lives on the Windows side
(a distro-side open of the live file raises `disk I/O error` or reads a stale copy):

    C:\\Users\\<user>\\Python312-64\\python.exe status_snapshot.py

Every print is ASCII-only on purpose: the Windows console is cp949 and a stray em dash
kills the script mid-run. Only the two argparse defaults need adjusting per machine.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import urllib.request


def get(bridge, path):
    with urllib.request.urlopen(bridge + path, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def trail_params(repo):
    """trailing_arm_pct / trailing_stop_pct from the project config, else 3% / 3%."""
    arm, trail = 0.03, 0.03
    try:
        sys.path.insert(0, repo)
        from trader_core.config import Config

        cfg = Config()
        arm = num(getattr(cfg, "trailing_arm_pct", arm), arm)
        trail = num(getattr(cfg, "trailing_stop_pct", trail), trail)
    except Exception as exc:  # the import is a nicety, never a gate
        print("note: config import failed ({0}); using arm/trail 3%/3%".format(exc))
    return arm, trail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=r"C:\Users\jhshi\trader-agent")
    ap.add_argument("--bridge", default="http://127.0.0.1:8100")
    args = ap.parse_args()
    arm, trail = trail_params(args.repo)
    today = dt.datetime.now().strftime("%Y-%m-%d")

    con = sqlite3.connect(args.repo + r"\journal\trade_journal.sqlite3")
    con.row_factory = sqlite3.Row
    open_rows = [dict(r) for r in con.execute(
        "SELECT id, ts, code, name, qty, price, stop_pct, tp_pct, hwm_price, screener "
        "FROM trades WHERE exit_ts IS NULL ORDER BY id")]
    closed = [dict(r) for r in con.execute(
        "SELECT code, name, qty, price, exit_price, exit_reason, pnl, exit_ts FROM trades "
        "WHERE exit_ts >= ? ORDER BY id", (today,))]
    con.close()

    positions = {p["code"]: p for p in get(args.bridge, "/positions").get("positions", [])}
    orders = get(args.bridge, "/orders").get("orders", [])
    balance = get(args.bridge, "/balance").get("balance", {})

    print("=== BROKER as of {0} ===".format(dt.datetime.now().isoformat(timespec="seconds")))
    print("equity {0} cash {1} securities {2} positions {3}".format(
        balance.get("equity"), balance.get("cash"),
        balance.get("total_eval_amount"), balance.get("positions_count")))

    print("=== CLOSED TODAY (journal) ===")
    realised = 0.0
    for r in closed:
        realised += num(r["pnl"])
        print("  {0} {1} qty {2} {3} -> {4}  pnl {5}  [{6}] {7}".format(
            r["code"], r["name"], r["qty"], r["price"], r["exit_price"],
            r["pnl"], r["exit_reason"], str(r["exit_ts"] or "")[11:19]))
    print("  realised total (journal, quote-priced): {0}".format(realised))

    print("=== OPEN ROWS vs BROKER ===")
    open_codes = {r["code"] for r in open_rows}
    phantoms, near = [], []
    for r in open_rows:
        code, entry = r["code"], num(r["price"])
        stop, hwm = num(r["stop_pct"]), num(r["hwm_price"], entry) or entry
        pos = positions.get(code)
        if pos is None:
            phantoms.append(code)
            print("  {0} {1} qty {2} entry {3:,.0f} :: NO BROKER POSITION (unconfirmed row)".format(
                code, r["name"], r["qty"], entry))
            continue
        mark = num(pos.get("eval_amount")) / max(1, int(pos.get("qty") or 1))
        armed = hwm >= entry * (1 + arm)
        line = entry * (1 - stop)
        if armed:
            line = max(line, hwm * (1 - trail))
        dist = (mark - line) / mark * 100 if mark else 0.0
        if dist <= 1.0:
            near.append((code, dist))
        print("  {0} {1} qty {2:>4} entry {3:>10,.0f} mark {4:>10,.0f} {5:+.2f}% "
              "stop {6:.2%} hwm {7:>10,.0f} armed {8} line {9:>10,.0f} to_line {10:+.2f}%".format(
                  code, r["name"], pos.get("qty"), entry, mark,
                  (mark / entry - 1) * 100 if entry else 0.0, stop, hwm, armed, line, dist))

    print("=== BROKER-ONLY (held, no open row: nothing monitors it) ===")
    broker_only = [c for c in positions if c not in open_codes]
    if broker_only:
        for code in broker_only:
            print("  {0}".format(code))
    else:
        print("  none")

    print("=== WORKING ORDERS ===")
    if not orders:
        print("  none")
    for o in orders:
        print("  order_no {0} {1} {2} qty {3} price {4} filled {5} cancelable {6}".format(
            o.get("order_no"), o.get("code"), o.get("name"), o.get("qty"),
            o.get("price"), o.get("filled_qty"), o.get("cancelable_qty")))

    print("=== FLAGS ===")
    unfilled = [o.get("code") for o in orders if num(o.get("filled_qty")) == 0]
    flagged = False
    if phantoms:
        flagged = True
        print("  unconfirmed rows (broker holds nothing): {0}".format(", ".join(phantoms)))
        print("  -> an unfilled working order above explains it; it is costing a book slot right now")
    if unfilled:
        flagged = True
        print("  working orders with filled_qty 0: {0}".format(
            ", ".join(str(c) for c in unfilled)))
    for code, dist in near:
        flagged = True
        print("  {0} is {1:+.2f}% from its exit line (next cycle may exit it)".format(code, dist))
    if not flagged:
        print("  none")
    print("note: 'mark' is the broker mark (eval_amount / qty), not a fresh quote; the monitor takes "
          "the LOWER of quote and mark, so a holding can sit closer to its line than this print shows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
