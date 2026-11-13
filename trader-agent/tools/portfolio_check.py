#!/usr/bin/env python3
"""Live portfolio + P&L check against the bridge and the trade journal.

Answers "what is my return right now" without trusting the journal alone:

* positions/prices come from the **broker** (via the bridge),
* entry prices/stop levels come from the **journal**,
* the day's return is measured against the loop's latched ``start_equity``,
* and any mismatch between journal open trades and broker positions is
  reported explicitly (a position sold/never bought shows up here instead of
  being silently averaged into a "return").

Usage::

    python tools/portfolio_check.py                 # human summary
    python tools/portfolio_check.py --json          # machine readable

Exit codes: 0 ok, 1 bridge/journal unavailable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runner.state import StateStore  # noqa: E402
from trader_core import BridgeClient  # noqa: E402
from trader_core.config import Config, now_kst  # noqa: E402
from trader_core.journal import Journal  # noqa: E402


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def collect(cfg: Config) -> dict:
    """Return the live portfolio picture as a plain dict."""
    bridge = BridgeClient(cfg.bridge_base_url, timeout=max(10.0, cfg.request_timeout))
    health = bridge.health()
    balance = bridge.balance()
    positions = bridge.positions()
    orders = bridge.open_orders()

    codes = [str(p.get("code")) for p in positions if p.get("code")]
    quotes = bridge.quotes(codes) if codes else {}

    rows = []
    cost = value = 0.0
    for position in positions:
        code = str(position.get("code") or "")
        qty = _num(position.get("qty"))
        entry = _num(position.get("avg_price"))
        current = _num((quotes.get(code) or {}).get("current"))
        pnl = (current - entry) * qty if current else 0.0
        cost += entry * qty
        value += (current or entry) * qty
        rows.append(
            {
                "code": code,
                "name": position.get("name", ""),
                "qty": qty,
                "entry": entry,
                "current": current,
                "value": current * qty,
                "pnl": pnl,
                "return_pct": ((current / entry - 1) * 100) if entry else 0.0,
            }
        )

    journal_rows = []
    journal_path = cfg.journal_path
    if os.path.exists(journal_path):
        journal = Journal(journal_path)
        try:
            for trade in journal.open_trades():
                journal_rows.append(
                    {
                        "code": trade.get("code"),
                        "qty": _num(trade.get("qty")),
                        "entry": _num(trade.get("price")),
                        "stop_pct": trade.get("stop_pct"),
                        "tp_pct": trade.get("tp_pct"),
                        "ts": trade.get("ts"),
                    }
                )
        finally:
            close = getattr(journal, "close", None)
            if callable(close):
                close()

    broker_codes = {row["code"] for row in rows}
    journal_codes = {row["code"] for row in journal_rows}
    state = StateStore(cfg.journal_path.replace("journal/trade_journal.sqlite3", "loop_state.json")).read()
    start_equity = state.get("daily", {}).get("start_equity")
    equity = _num(balance.get("equity"))
    cash = _num(balance.get("cash"))

    return {
        "ts": now_kst().isoformat(),
        "connected": bool(health.get("connected")),
        "cash": cash,
        "equity": equity,
        "start_equity": start_equity,
        "day_pnl": (equity - start_equity) if start_equity else None,
        "day_return_pct": ((equity / start_equity - 1) * 100) if start_equity else None,
        "positions": rows,
        "open_orders": orders,
        "journal_open_trades": journal_rows,
        "cost": cost,
        "value": value,
        "unrealised_pnl": value - cost,
        "journal_only_codes": sorted(journal_codes - broker_codes),
        "broker_only_codes": sorted(broker_codes - journal_codes),
        "broker_total_eval": balance.get("broker_total_eval"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="portfolio_check.py", description=__doc__)
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    args = parser.parse_args(argv)

    cfg = Config()
    try:
        data = collect(cfg)
    except Exception as exc:  # noqa: BLE001 - report, don't traceback
        print("portfolio check failed: {0}".format(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0

    print("portfolio @ {0}   bridge connected={1}".format(
        data["ts"][:19], data["connected"]))
    print("현금 주문가능 {:>12,.0f}   equity(현금+주식) {:>12,.0f}".format(
        data["cash"], data["equity"]))
    if data["start_equity"]:
        print("당일 시작 {:>12,.0f}   당일 손익 {:>+12,.0f} ({:+.3f}%)".format(
            data["start_equity"], data["day_pnl"], data["day_return_pct"]))
    if data["broker_total_eval"]:
        print("(참고: CpTd6033 총평가 헤더 = {:,.0f} - 주식평가만이고 equity 아님)".format(
            data["broker_total_eval"]))
    print()
    if data["positions"]:
        print("{:<8} {:<14} {:>5} {:>9} {:>9} {:>10} {:>8}".format(
            "code", "name", "qty", "진입", "현재", "평가손익", "수익률"))
        for row in data["positions"]:
            print("{:<8} {:<14} {:>5.0f} {:>9,.0f} {:>9,.0f} {:>+10,.0f} {:>+7.2f}%".format(
                row["code"], str(row["name"])[:14], row["qty"], row["entry"],
                row["current"], row["pnl"], row["return_pct"]))
        print("{:<31} 투입 {:>9,.0f}  평가 {:>9,.0f}  평가손익 {:>+9,.0f}".format(
            "합계", data["cost"], data["value"], data["unrealised_pnl"]))
    else:
        print("보유 포지션 없음")
    if data["open_orders"]:
        print()
        print("미체결 주문:")
        for order in data["open_orders"]:
            print("  {} {} {}주 @ {:,.0f} (주문번호 {}, 체결 {})".format(
                order.get("code"), order.get("name"), _num(order.get("qty")),
                _num(order.get("price")), order.get("order_no"),
                _num(order.get("filled_qty"))))
    if data["journal_only_codes"] or data["broker_only_codes"]:
        print()
        print("저널/브로커 불일치 (정정 필요):")
        if data["journal_only_codes"]:
            print("  저널에만 있음(실제로는 미보유): {0}".format(data["journal_only_codes"]))
        if data["broker_only_codes"]:
            print("  브로커에만 있음(저널 누락): {0}".format(data["broker_only_codes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
