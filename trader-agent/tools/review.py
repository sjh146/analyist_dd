#!/usr/bin/env python3
"""Self-review CLI: read the trade journal and retune strategy parameters.

This is the "수정" half of the autonomous loop.  Run it after the session (or
from cron): it reads the journal, computes realised performance, shrinks it
toward conservative priors, moves the R1 entry bar in bounded steps and writes
``strategy_params.json`` -- which both the engine-side rank feed
(``trader_core/rankfeed.py``, read on every cycle) and the loop-side rule
thresholds (``runner.config.RunnerConfig.apply_params``, read at startup) pick
up without any code change.

Usage::

    python tools/review.py                       # review + write params
    python tools/review.py --dry-run             # show the decision only
    python tools/review.py --period-days 7 --min-samples 10
    python tools/review.py --journal journal/trade_journal.sqlite3

Exit codes: 0 ok (including "nothing to learn yet"), 2 usage, 1 runtime error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trader_core.config import Config, KST  # noqa: E402
from trader_core.journal import Journal  # noqa: E402
from trader_core.review import review  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review.py", description=__doc__)
    parser.add_argument(
        "--journal", metavar="PATH", default=None,
        help="journal sqlite path (default: trader-core journal_path)",
    )
    parser.add_argument(
        "--params", metavar="PATH", default=None,
        help="params file to read/update (default: strategy_params.json)",
    )
    parser.add_argument(
        "--screener", metavar="NAME", default=None,
        help="screener key whose entry bar is tuned (default: rank_screener)",
    )
    parser.add_argument(
        "--period-days", type=int, default=30,
        help="rolling window in days for realised performance (default 30)",
    )
    parser.add_argument(
        "--min-samples", type=int, default=5,
        help="closed trades required before any parameter moves (default 5)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the decision without writing the params file",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the result as JSON",
    )
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    defaults = Config()
    journal_path = args.journal or defaults.journal_path
    params_path = args.params or defaults.rank_params_path
    screener = args.screener or defaults.rank_screener

    if not os.path.exists(journal_path):
        print("journal not found: {0} (nothing to review yet)".format(journal_path))
        return EXIT_OK

    try:
        journal = Journal(journal_path)
    except Exception as exc:  # noqa: BLE001 - surface any sqlite/OS failure
        print("journal open failed: {0}".format(exc), file=sys.stderr)
        return EXIT_ERROR

    try:
        result = review(
            journal,
            params_path,
            screener=screener,
            period_days=args.period_days,
            min_samples=args.min_samples,
            apply=not args.dry_run,
        )
    finally:
        close = getattr(journal, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return EXIT_OK

    stats = result["stats"]
    print("review @ {0}".format(KST))
    print("journal : {0}".format(journal_path))
    print("screener: {0}".format(screener))
    print("closed trades: {trades}  wins={wins} losses={losses} "
          "win_rate={win_rate:.3f}".format(**stats))
    print("avg win {avg_win_pct:.2f}%  avg loss {avg_loss_pct:.2f}%  "
          "PnL {total_pnl:,.0f}  PF={pf}".format(
              pf=("n/a" if stats["profit_factor"] is None
                  else "{0:.2f}".format(stats["profit_factor"])),
              **stats))
    for line in result["rationale"]:
        print("  - {0}".format(line))
    if result["written"]:
        print("wrote: {0}".format(result["written"]))
    else:
        print("wrote: (nothing - dry-run or no closed trades)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
