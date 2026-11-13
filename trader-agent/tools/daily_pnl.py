#!/usr/bin/env python3
"""Broker-side realised P&L for today (CpTd6032 체결기준 당일 매매 손익).

Run with the **32-bit** interpreter (CybosPlus COM):

    C:\\Users\\jhshi\\Python311-32\\python.exe tools\\daily_pnl.py

Why this exists: the journal records what the *core* believes happened, and a
market-order echo of ``price: 0`` once made it record a full-position loss on an
exit that actually filled near the quote.  This tool asks the broker instead:
per-stock ``평균매도단가`` / ``매도실현손익`` plus the account summary, which is
the authoritative number for reports and for the self-review step.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.orders import fetch_daily_pnl  # noqa: E402
from bridge.plus import BridgeError  # noqa: E402


def main() -> int:
    try:
        data = fetch_daily_pnl(None)
    except BridgeError as exc:
        print("daily pnl failed: {0}".format(exc))
        return 1

    summary = data.get("summary") or {}
    print("=== 계좌 요약 (체결기준 당일) ===")
    print("  잔고평가손익 합계 : {0}".format(summary.get("eval_pnl")))
    print("  매도실현손익 합계 : {0}".format(summary.get("realized_pnl")))
    print("  수익률           : {0}".format(summary.get("return_rate")))

    items = data.get("items") or []
    print()
    print("=== 종목별 ({0}건) ===".format(len(items)))
    header = "{:<8} {:<14} {:>6} {:>8} {:>8} {:>10} {:>10}"
    print(header.format("code", "name", "매수", "매도", "잔고", "평균매도", "실현손익"))
    for item in items:
        print(
            "{:<8} {:<14} {:>6} {:>8} {:>8} {:>10} {:>10}".format(
                str(item.get("code", "")),
                str(item.get("name", ""))[:14],
                str(item.get("buy_qty", "")),
                str(item.get("sell_qty", "")),
                str(item.get("today_balance_qty", "")),
                str(item.get("avg_sell_price", "")),
                str(item.get("realized_pnl", "")),
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
