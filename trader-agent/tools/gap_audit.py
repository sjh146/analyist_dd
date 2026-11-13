"""Audit a day's 1-minute closes against every open position's exit line.

Answers the question a monitoring gap leaves behind: "did a stop / trailing
line get breached while nothing was watching, and if so when?"  Data source is
Naver's 1-minute chart endpoint (EUC-KR XML), so this works from the WSL side
with no bridge and no broker requests.

    python tools\\gap_audit.py                       # today, whole session
    python tools\\gap_audit.py --date 2026-09-21
    python tools\\gap_audit.py --window gap=1246-1303
    python tools\\gap_audit.py --window "loop off=1000-1200" --window after=1200-1530

Caveat printed with every run: the series is 1-minute *closes*, so an intrabar
spike inside a minute is invisible; a "no breach" result bounds the loss but is
not millisecond proof.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import urllib.request

CHART_URL = ("https://fchart.stock.naver.com/sise.nhn?symbol={code}"
             "&timeframe=minute&count=2000&requestType=0")
DEFAULT_JOURNAL = "journal/trade_journal.sqlite3"
SESSION = (900, 1530)


def minute_closes(code: str, day: str) -> dict:
    """Return ``{HHMM: close}`` for *day* (YYYYMMDD) for *code*."""
    url = CHART_URL.format(code=code)
    raw = urllib.request.urlopen(url, timeout=30).read().decode("euc-kr", "replace")
    out = {}
    for chunk in raw.split("<item data=")[1:]:
        fields = chunk.split('"')[1].split("|")
        stamp = fields[0]
        if not stamp.startswith(day):
            continue
        try:
            out[int(stamp[8:12])] = float(fields[4])
        except (IndexError, TypeError, ValueError):
            continue
    return out


def exit_line(row: dict) -> float:
    """The line the monitor would actually act on for this journal row."""
    entry = float(row["price"])
    stop = float(row["stop_pct"] or 0)
    hwm = float(row["hwm_price"] or entry)
    line = entry * (1 - stop)
    if hwm >= entry * 1.03:  # trailing stop armed (arm 3 %, trail 3 %)
        line = max(line, hwm * 0.97)
    return line


def parse_window(text: str) -> tuple:
    if "=" in text:
        label, span = text.split("=", 1)
    else:
        label, span = text, text
    start, _, end = span.partition("-")
    return label.strip(), int(start), int(end)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today KST)")
    parser.add_argument("--journal", default=DEFAULT_JOURNAL)
    parser.add_argument("--window", action="append", default=None,
                        help="label=HHMM-HHMM (repeatable; default: session)")
    args = parser.parse_args(argv)

    day = args.date or dt.datetime.now(
        dt.timezone(dt.timedelta(hours=9))
    ).strftime("%Y-%m-%d")
    stamp = day.replace("-", "")
    windows = [parse_window(w) for w in args.window] if args.window else [
        ("session 0900-1530", SESSION[0], SESSION[1])
    ]

    con = sqlite3.connect(args.journal)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT code, qty, price, stop_pct, hwm_price FROM trades "
        "WHERE exit_ts IS NULL ORDER BY code"
    )]
    if not rows:
        print("no open positions in the journal")
        return 0

    header = "{0:<8}{1:>10}{2:>10}  ".format("code", "entry", "line")
    header += "  ".join("{0:>16}".format(w[0]) for w in windows)
    print(day + "  (1-minute closes)")
    print(header)
    breaches = []
    for row in rows:
        code = row["code"]
        line = exit_line(row)
        try:
            closes = minute_closes(code, stamp)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print("{0:<8} chart fetch failed: {1}".format(code, str(exc)[:60]))
            continue
        cells = []
        for label, lo, hi in windows:
            vals = [(k, v) for k, v in closes.items() if lo <= k <= hi]
            if not vals:
                cells.append("{0:>16}".format("-"))
                continue
            lowest = min(v for _, v in vals)
            flag = "BREACH" if lowest <= line else "ok"
            cells.append("{0:>10,.0f} {1:<5}".format(lowest, flag))
            if lowest <= line:
                hits = sorted(k for k, v in vals if v <= line)
                breaches.append((code, label, line, lowest, hits))
        print("{0:<8}{1:>10,.0f}{2:>10,.0f}  ".format(
            code, float(row["price"]), line) + "  ".join(cells))

    print()
    if not breaches:
        print("no exit line was touched in the selected window(s)")
    for code, label, line, lowest, hits in breaches:
        print("{0} BREACHED in '{1}': line {2:,.0f}, low {3:,.0f}, "
              "minutes {4}".format(
                  code, label, line, lowest,
                  ", ".join("{0:04d}".format(h) for h in hits[:15])))
    print("note: 1-minute closes only; an intrabar spike inside a minute is "
          "invisible, and the high-water mark used here is the one recorded now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
