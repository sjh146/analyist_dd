"""Measure a live ranking window: which ranks are actually buyable right now?

Companion to the section "When the filter and the selection window sample the same end".
A rank list sorted by the capped metric puts the most extended names at the head, so the
ceiling, the pullback guard and the shortlist depth can all miss each other.  Run this
before picking or changing any of those values: it quotes the head of the page and prints
the move, the distance below the day's high, and the volume per row, so you can see which
filter kills which row.

Requires the trader-agent repo (imports ``trader_core``); run it from the repo root with the
64-bit interpreter, since only the bridge needs the 32-bit one::

    C:\Users\jhshi\Python312-64\python.exe scripts\rank_probe.py
    C:\Users\jhshi\Python312-64\python.exe scripts\rank_probe.py --pct-to 15 --head 8

Read-only.  Cost: one rank request plus one quote per probed row, so keep ``--head`` small
(the venue throttles per second, and the same quotes are the loop's own per-cycle budget).
Output goes to stdout *and* to ``--out`` as UTF-8, because a cp949 console mangles Korean
names.
"""

from __future__ import annotations

import argparse
import io
import os
import sys

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trader_core.bridge_client import BridgeClient  # noqa: E402
from trader_core.rankfeed import pct_change  # noqa: E402


HEADER = "{0:>4} {1:>8} {2:<14} {3:>9} {4:>8} {5:>9} {6:>12}"


def _cell(value, pattern="{0:,.0f}"):
    if value is None:
        return "-"
    try:
        return pattern.format(float(value))
    except (TypeError, ValueError):
        return "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--limit", type=int, default=60, help="rank rows to request")
    parser.add_argument("--pct-from", type=int, default=0)
    parser.add_argument("--pct-to", type=int, default=30,
                        help="window upper bound; set it to the ceiling under test")
    parser.add_argument("--sort", type=int, default=21, help="21 = day-move descending")
    parser.add_argument("--head", type=int, default=8,
                        help="rows to quote (== quote requests)")
    parser.add_argument("--out", default="tmp_rank_probe.txt")
    args = parser.parse_args()

    client = BridgeClient(args.base_url)
    ranking = client.rank(
        limit=args.limit, direction="up", pct_from=args.pct_from,
        pct_to=args.pct_to, sort=args.sort,
    )
    rows = [row for row in (ranking.get("items") or []) if isinstance(row, dict)]

    out = io.open(args.out, "w", encoding="utf-8")

    def emit(line=""):
        out.write(line + "\n")
        print(line)

    emit("window pct {0}..{1}%  rows {2}  pages {3}".format(
        args.pct_from, args.pct_to, len(rows), ranking.get("pages")))
    head = rows[: max(1, args.head)]
    codes = [str(row.get("code", "")).strip() for row in head]
    quotes = client.quotes(codes) if codes else {}
    emit("quotes returned {0}/{1}".format(len(quotes), len(codes)))
    emit(HEADER.format("rank", "code", "name", "current", "move %", "off high", "volume"))
    for index, row in enumerate(head, start=1):
        code = str(row.get("code", "")).strip()
        quote = quotes.get(code) or {}
        current = quote.get("current")
        move = pct_change(current, row.get("diff"), row.get("diff_sign"))
        high = quote.get("high")
        off_high = None
        if high and current:
            try:
                off_high = (float(high) - float(current)) / float(high) * 100.0
            except (TypeError, ValueError):
                off_high = None
        emit(HEADER.format(
            index,
            code,
            str(row.get("name", ""))[:14],
            _cell(current),
            "-" if move is None else "{0:+.2f}".format(move),
            "-" if off_high is None else "{0:.2f}".format(off_high),
            "{0:,}".format(int(row.get("volume") or 0)),
        ))
    emit()
    emit("table written to {0} (UTF-8; read it back when the console mangles the names)".format(
        os.path.abspath(args.out)))
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
