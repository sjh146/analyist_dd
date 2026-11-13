#!/usr/bin/env python3
"""Reconcile the trade journal with the broker (report by default).

Run this whenever the journal and the broker may have drifted (a session
dropped mid-cycle, an order response looked like an error but filled, an exit
was journaled at a bogus price).  It never invents prices: closes need the
broker's quote or an explicit ``--exit-price``.

Usage::

    python tools/reconcile_journal.py                    # report only
    python tools/reconcile_journal.py --json
    python tools/reconcile_journal.py --apply            # repair (uses quotes)
    python tools/reconcile_journal.py --apply --exit-price 3755

Exit codes: 0 ok (even when nothing needed fixing), 1 bridge/journal failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader_core import BridgeClient  # noqa: E402
from trader_core.config import Config, now_kst  # noqa: E402
from trader_core.journal import Journal  # noqa: E402
from trader_core.reconcile import reconcile  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile_journal.py", description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the repairs (default: report only)")
    parser.add_argument("--exit-price", type=float, default=None,
                        help="price to use when closing a position the broker no "
                             "longer holds (default: the live quote)")
    parser.add_argument("--fix-exit", action="append", default=[], metavar="CODE=PRICE",
                        help="correct a bogus (<=0) exit price for CODE, e.g. "
                             "--fix-exit 026910=3747 (repeatable; use the broker's "
                             "평균매도단가 from tools/daily_pnl.py)")
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    args = parser.parse_args(argv)

    exit_prices = {}
    for item in args.fix_exit:
        if "=" not in item:
            print("bad --fix-exit '{0}': expected CODE=PRICE".format(item), file=sys.stderr)
            return 1
        code, _, raw = item.partition("=")
        try:
            exit_prices[code.strip()] = float(raw)
        except ValueError:
            print("bad --fix-exit '{0}': price is not a number".format(item), file=sys.stderr)
            return 1

    cfg = Config()
    try:
        bridge = BridgeClient(cfg.bridge_base_url, timeout=max(10.0, cfg.request_timeout))
        positions = bridge.positions()
        orders = bridge.open_orders()
        codes = [str(p.get("code")) for p in positions if p.get("code")]
        quotes = bridge.quotes(codes) if codes else {}
    except Exception as exc:  # noqa: BLE001 - report, don't traceback
        print("bridge unavailable: {0}".format(exc), file=sys.stderr)
        return 1

    if not os.path.exists(cfg.journal_path):
        print("journal not found: {0}".format(cfg.journal_path))
        return 0

    journal = Journal(cfg.journal_path)
    try:
        result = reconcile(
            journal, positions, quotes=quotes, open_orders=orders,
            exit_price=args.exit_price, exit_prices=exit_prices,
            apply=args.apply,
        )
    finally:
        close = getattr(journal, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    print("reconcile @ {0}   applied={1}".format(now_kst().isoformat()[:19], result["applied"]))
    print("broker positions : {0}".format(result["broker_codes"] or "(none)"))
    print("journal open     : {0}".format(result["journal_open_codes"] or "(none)"))
    for label, key in (("재개(reopen)", "reopened"),
                       ("청산(close)", "closed"),
                       ("편입(adopt)", "adopted"),
                       ("정정(fix exit)", "corrected"),
                       ("부분체결 정정", "qty_fixed"),
                       ("미체결 정리", "unfilled")):
        for row in result[key]:
            print("  {0}: {1}".format(label, json.dumps(row, ensure_ascii=False, default=str)))
    for row in result["bogus_exits"]:
        print("  0원청산 발견: {0} (trade {1}, 기록된 pnl {2:,.0f}) - --fix-exit {0}=PRICE".format(
            row["code"], row["trade_id"], row["pnl_wrong"]))
    for row in result["needs_price"]:
        print("  가격필요: {0} (trade {1}, {2}주) - --exit-price 또는 브리지 필요".format(
            row["code"], row["trade_id"], row["qty"]))
    if result["notes"]:
        print()
        for note in result["notes"]:
            print("  - {0}".format(note))
    if not any(
        result[key]
        for key in ("reopened", "closed", "adopted", "corrected", "unfilled",
                    "bogus_exits", "needs_price")
    ):
        print("\n불일치 없음 - 저널과 브로커가 일치합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
