"""Reopen a journalled exit that the broker never confirmed (see OPERATIONS).

2026-09-28 13:49: the monitor fired a 시장가 매도 for 067630 (order 2714) after
the take-profit level was breached, and the exit was journalled at the reference
quote (3,405, +18,090) even though the broker still holds 27 shares and the order
is unfilled (CpTd6032 realised P&L for the day: 0).  That left the journal with
9 open rows against 10 broker positions, and inflated today's closed_pnl.

This script reopens the trade row (exit_* back to NULL) so the journal matches
the broker again, and stores the abandoned exit record inside algo_json
("reopened_exit") for the record.  It does NOT place or cancel any order.

Usage (64-bit Python, loop may be running - single UPDATE, WAL-safe):
    python tools/reopen_phantom_exit.py --code 067630 --dry-run
    python tools/reopen_phantom_exit.py --code 067630 --yes
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "journal" / "trade_journal.sqlite3"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--code", required=True, help="종목코드 (예: 067630)")
    ap.add_argument("--yes", action="store_true", help="실제로 반영")
    ap.add_argument("--dry-run", action="store_true", help="현재 상태만 출력")
    args = ap.parse_args(argv)

    if not JOURNAL.exists():
        print("journal not found: {0}".format(JOURNAL))
        return 1

    conn = sqlite3.connect(str(JOURNAL), timeout=15.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select * from trades where code = ? and exit_ts is not null "
        "order by exit_ts desc",
        (args.code,),
    ).fetchall()
    open_rows = conn.execute(
        "select id, code, qty, price, ts from trades where exit_ts is null order by ts"
    ).fetchall()

    print("closed rows for {0}: {1}".format(args.code, len(rows)))
    for r in rows:
        print(
            "  id={0} qty={1} entry={2} exit_ts={3} exit_price={4} reason={5} pnl={6}".format(
                r["id"], r["qty"], r["price"], r["exit_ts"], r["exit_price"],
                r["exit_reason"], r["pnl"],
            )
        )
    print("open rows now: {0}".format(len(open_rows)))

    if args.dry_run or not args.yes:
        if not rows:
            return 0
        if not args.yes:
            print("dry-run (pass --yes to apply)")
        return 0

    if len(rows) != 1:
        print("refusing: expected exactly one closed row for {0}, found {1}".format(args.code, len(rows)))
        return 2

    row = rows[0]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT / "journal" / "backup_{0}".format(stamp)
    backup_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(JOURNAL) + suffix)
        if src.exists():
            shutil.copy2(src, backup_dir / src.name)
    print("backup -> {0}".format(backup_dir))

    algo = {}
    try:
        algo = json.loads(row["algo_json"] or "{}")
    except Exception:  # noqa: BLE001 - keep whatever was there
        algo = {"_raw": row["algo_json"]}
    algo["reopened_exit"] = {
        "ts": row["exit_ts"],
        "price": row["exit_price"],
        "reason": row["exit_reason"],
        "pnl": row["pnl"],
        "note": "broker never confirmed the fill (order left working); journal reopened",
        "reopened_at": datetime.now().isoformat(),
    }

    conn.execute(
        "update trades set exit_ts = null, exit_price = null, exit_reason = null, "
        "pnl = null, algo_json = ? where id = ?",
        (json.dumps(algo, ensure_ascii=False), row["id"]),
    )
    conn.commit()
    after = conn.execute("select count(*) from trades where exit_ts is null").fetchone()[0]
    print("reopened id={0}; open rows now {1}".format(row["id"], after))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
