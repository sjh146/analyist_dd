"""Set stop/take-profit levels on the journal's OPEN trades.

Live entries record ``stop_pct`` / ``tp_pct`` on the trade row, and the
monitor reads those row values *before* falling back to the config defaults
(``trader_core/monitor.py::_levels``).  Changing the config therefore does not
move an already-open position: its level has to be edited on the row.

Usage (64-bit interpreter, run from the repo root):

    python tools\\set_open_tp.py --tp 0.07              :: all open trades
    python tools\\set_open_tp.py --tp 0.07 --codes 073010,452450
    python tools\\set_open_tp.py --sl 0.07 --tp 0.07 --dry-run

Only rows with ``exit_ts IS NULL`` are touched.  Closed trades are history and
are never rewritten -- the levels that produced a fill must stay auditable.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

DEFAULT_JOURNAL = os.path.join("journal", "trade_journal.sqlite3")


def _pct(value: str) -> float:
    number = float(value)
    if number > 1.0:  # accept 7 as well as 0.07
        number = number / 100.0
    if not 0 < number < 1:
        raise argparse.ArgumentTypeError("level must be between 0 and 1 (or 0-100)")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default=DEFAULT_JOURNAL)
    parser.add_argument("--tp", type=_pct, default=None, help="take-profit, e.g. 0.07 or 7")
    parser.add_argument("--sl", type=_pct, default=None, help="stop-loss, e.g. 0.07 or 7")
    parser.add_argument("--codes", default="", help="comma separated; default = all open")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.tp is None and args.sl is None:
        parser.error("nothing to do: pass --tp and/or --sl")

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    where = "exit_ts IS NULL"
    params = []
    if codes:
        where += " AND code IN ({0})".format(",".join("?" * len(codes)))
        params.extend(codes)

    conn = sqlite3.connect(args.journal, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = list(conn.execute(
            "SELECT id, code, name, qty, price, stop_pct, tp_pct FROM trades WHERE "
            + where + " ORDER BY id",
            params,
        ))
        if not rows:
            print("no open trades matched")
            return 1
        print("open trades matched: {0}".format(len(rows)))
        print("{0:>4} {1:8} {2:14} {3:>9} {4:>9}".format("id", "code", "name", "stop", "tp"))
        for row in rows:
            print("{0:>4} {1:8} {2:14} {3:>9} {4:>9}".format(
                row["id"], row["code"], row["name"] or "",
                _fmt(row["stop_pct"]), _fmt(row["tp_pct"]),
            ))
        if args.dry_run:
            print("dry-run: nothing written")
            return 0
        sets, values = [], []
        if args.sl is not None:
            sets.append("stop_pct = ?")
            values.append(args.sl)
        if args.tp is not None:
            sets.append("tp_pct = ?")
            values.append(args.tp)
        ids = [row["id"] for row in rows]
        conn.execute(
            "UPDATE trades SET {0} WHERE id IN ({1})".format(
                ", ".join(sets), ",".join("?" * len(ids))
            ),
            values + ids,
        )
        conn.commit()
        print("updated {0} trade(s)".format(len(ids)))
        for row in conn.execute(
            "SELECT id, code, stop_pct, tp_pct FROM trades WHERE id IN ({0}) ORDER BY id".format(
                ",".join("?" * len(ids))
            ),
            ids,
        ):
            print("  id {0} {1}: stop {2} tp {3}".format(
                row[0], row[1], _fmt(row[2]), _fmt(row[3])
            ))
    finally:
        conn.close()
    return 0


def _fmt(value) -> str:
    if value is None:
        return "-"
    return "{0:.1%}".format(float(value))


if __name__ == "__main__":
    sys.exit(main())
