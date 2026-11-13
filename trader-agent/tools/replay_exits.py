"""Exit-rule replay: what would other stop / take-profit / trail / hold-time settings have produced?

Entries are FIXED (code, entry minute, entry price, quantity from the journal);
only the exit decision is replayed on Naver 1-minute closes, one rule set at a
time, so a tuning decision can be compared against the rule actually in force.
Read-only: no bridge requests, no journal writes.

    C:\\Users\\jhshi\\Python312-64\\python.exe tools\\replay_exits.py
    ... tools\\replay_exits.py --rules '{"arm6/trail5": {"arm": 0.06, "trail": 0.05}}'
    ... tools\\replay_exits.py --codes 030530,010580 --out tmp_replay.txt

Rule keys: ``arm``/``trail`` (fractions), ``tp_mult`` (x the trade's own stop),
``tp_abs`` (absolute), ``stop_factor`` (rebuild the vol-scaled stop:
max(7%, factor x day range) capped by ``stop_cap``, default 20%), ``fixed_stop``
(absolute), ``stop_cap`` (absolute ceiling on the vol-scaled stop),
``hold_sessions`` (int: force the exit at the last minute of the Nth session
counted from the entry session -- 1 = same-day close, 2 = next session's close;
absent/0 = hold until the rule or the real exit session fires),
``time_stop`` ({"sessions": N, "gain": f}: exit at the end of session N when the
position is not up at least ``gain`` -- a momentum-decay cut).

Turnover is reported per rule as the average holding minutes, so a rule can be
judged on both PnL and how fast the capital comes back.

Stated limitations, printed with every run:
  * 1-minute CLOSES: the intrabar path inside a minute is unknown, so a stop and a
    take-profit touched in the same minute resolve in the monitor's own order.
  * sessions only (09:00-15:30 KST): an overnight gap is not a price path.
  * entries and quantity as traded, so a narrower stop's larger equal-risk position
    size is not in the table (that does not change KRW risk per stop-out).
  * the chart endpoint carries only recent sessions; older trades are listed as
    not covered, and the replay's absolute total does not reproduce the live fills
    (coarse prices lose the live trail's timing) -- read the DELTAS, not the level.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
import urllib.request

CHART = ("https://fchart.stock.naver.com/sise.nhn?symbol={code}"
         "&timeframe=minute&count=4000&requestType=0")
DEFAULT_JOURNAL = "journal/trade_journal.sqlite3"

DEFAULT_RULES = {
    "R0 현재 (설정 그대로)": {},
    "arm2/trail2 (좁은 추격)": {"arm": 0.02, "trail": 0.02},
    "stop cap 6%": {"stop_cap": 0.06},
    "고정 7%/7%": {"fixed_stop": 0.07, "tp_abs": 0.07},
    "당일 청산": {"hold_sessions": 1},
    "5분 보유": {"hold_minutes": 5},
    "30분 보유": {"hold_minutes": 30},
    "60분 보유": {"hold_minutes": 60},
    "익일 청산": {"hold_sessions": 2},
    "3일 청산": {"hold_sessions": 3},
    "시간손절 1일 +1%": {"time_stop": {"sessions": 1, "gain": 0.01}},
    "시간손절 2일 +3%": {"time_stop": {"sessions": 2, "gain": 0.03}},
    "시간손절 2일 +5%": {"time_stop": {"sessions": 2, "gain": 0.05}},
    "시간손절 3일 +3%": {"time_stop": {"sessions": 3, "gain": 0.03}},
    "보합청산 2일 +-1%": {"flat_stop": {"sessions": 2, "band": 0.01}},
    "보합청산 2일 +-2%": {"flat_stop": {"sessions": 2, "band": 0.02}},
    "보합청산 3일 +-3%": {"flat_stop": {"sessions": 3, "band": 0.03}},
    "시간손절 2일 +3% + arm2/trail2": {"time_stop": {"sessions": 2, "gain": 0.03},
                                       "arm": 0.02, "trail": 0.02},
    "보합2일 + arm2/trail2": {"flat_stop": {"sessions": 2, "band": 0.02},
                              "arm": 0.02, "trail": 0.02},
}


def minute_series(code: str):
    raw = urllib.request.urlopen(CHART.format(code=code), timeout=40).read() \
        .decode("euc-kr", "replace")
    out = []
    for chunk in raw.split("<item data=")[1:]:
        fields = chunk.split('"')[1].split("|")
        try:
            stamp = fields[0]
            if 900 <= int(stamp[8:12]) <= 1530:
                out.append((stamp, float(fields[4])))
        except (IndexError, TypeError, ValueError):
            continue
    return out


def session_stamps(series):
    """Return ``{session_date: [stamps...]}`` in order (a 'session' = one day)."""
    days = {}
    for stamp, _ in series:
        days.setdefault(stamp[:8], []).append(stamp)
    return days


def held_minutes(entry_stamp, exit_stamp, days):
    """Minutes of *session* time between the entry and the exit stamp."""
    total = 0
    for day, stamps in days.items():
        for stamp in stamps:
            if entry_stamp <= stamp <= exit_stamp:
                total += 1
    return total


def simulate(entry, stop, tp, arm, trail, series, entry_stamp, end_stamp=None,
             hold_sessions=0, time_stop=None, flat_stop=None, days=None,
             hold_minutes=0, time_stop_minutes=None):
    """Replay one trade; return ``(reason, price, exit_stamp)``."""
    stop_line = entry * (1 - stop)
    tp_line = entry * (1 + tp)
    hwm = entry
    days = days if days is not None else session_stamps(series)
    sessions = [d for d in sorted(days) if d >= entry_stamp[:8]]
    forced = None
    if hold_sessions and len(sessions) >= int(hold_sessions):
        # Only fire once the Nth session actually exists in the data: falling
        # back to the last bar would make a 5-session cap exit a 2-day-old
        # position and corrupt the rule comparison.
        forced = sessions[int(hold_sessions) - 1]
    ts_day = None
    if time_stop and time_stop.get("sessions") and \
            len(sessions) >= int(time_stop["sessions"]):
        ts_day = sessions[int(time_stop["sessions"]) - 1]
    fs_day = None
    if flat_stop and flat_stop.get("sessions") and \
            len(sessions) >= int(flat_stop["sessions"]):
        fs_day = sessions[int(flat_stop["sessions"]) - 1]

    seen = 0  # session minutes since the entry (each bar is one minute)
    for stamp, close in series:
        if stamp < entry_stamp:
            continue
        if end_stamp and stamp > end_stamp:
            break
        seen += 1
        if close <= stop_line:
            return "stop", close, stamp
        if close >= tp_line:
            return "tp", close, stamp
        if close > hwm:
            hwm = close
        if trail > 0 and hwm >= entry * (1 + arm) and close <= hwm * (1 - trail):
            return "trail", close, stamp
        # Minute-scale rules: a flat time box, or a momentum-decay cut.
        if hold_minutes and seen >= int(hold_minutes):
            return "hold{0}m".format(int(hold_minutes)), close, stamp
        if time_stop_minutes and time_stop_minutes.get("minutes") and \
                seen >= int(time_stop_minutes["minutes"]):
            gain = float(time_stop_minutes.get("gain") or 0.0)
            if close < entry * (1 + gain):
                return "time_stop_min", close, stamp
        # Session rules fire on the LAST minute of their session (a close fill).
        last_of_day = days[stamp[:8]][-1]
        if forced and stamp[:8] == forced and stamp == last_of_day:
            return "hold{0}".format(hold_sessions), close, stamp
        if ts_day and time_stop and stamp[:8] == ts_day and stamp == last_of_day:
            gain = float(time_stop.get("gain") or 0.0)
            if close < entry * (1 + gain):
                return "time_stop", close, stamp
        if fs_day and flat_stop and stamp[:8] == fs_day and stamp == last_of_day:
            band = float(flat_stop.get("band") or 0.0)
            if abs(close / entry - 1) <= band:
                return "flat_stop", close, stamp
    if series:
        return "open", series[-1][1], series[-1][0]
    return "open", entry, entry_stamp


def levels(row, cfg):
    stop = float(row["stop_pct"] or 0.07)
    cap = float(cfg.get("stop_cap") or 0.20)
    if "fixed_stop" in cfg:
        stop = cfg["fixed_stop"]
    elif "stop_factor" in cfg:
        try:
            rng = (json.loads(row["algo_json"] or "{}").get("candidate") or {}) \
                .get("day_range_pct")
        except ValueError:
            rng = None
        if rng:
            stop = min(max(0.07, cfg["stop_factor"] * float(rng) / 100.0), cap)
    elif "stop_cap" in cfg:
        stop = min(stop, cap)
    tp = stop * cfg["tp_mult"] if "tp_mult" in cfg else stop
    if "tp_abs" in cfg:
        tp = cfg["tp_abs"]
    return stop, tp


def stamp_of(ts):
    return str(ts)[:16].replace("-", "").replace("T", "").replace(":", "")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default=DEFAULT_JOURNAL)
    parser.add_argument("--rules", default=None,
                        help="JSON object {label: {cfg...}} replacing the default table")
    parser.add_argument("--codes", default=None, help="comma separated subset")
    parser.add_argument("--out", default=None, help="write the report here (UTF-8)")
    parser.add_argument("--detail", action="store_true",
                        help="per-trade session-close path (pct vs entry)")
    args = parser.parse_args(argv)

    rules = dict(DEFAULT_RULES)
    if args.rules:
        rules = json.loads(args.rules)
    wanted = set(c.strip() for c in args.codes.split(",")) if args.codes else None

    con = sqlite3.connect(args.journal)
    con.row_factory = sqlite3.Row
    closed = [dict(r) for r in con.execute(
        "SELECT code, qty, price, stop_pct, ts, exit_ts, exit_price, pnl, algo_json "
        "FROM trades "
        "WHERE exit_ts IS NOT NULL ORDER BY exit_ts")]
    opens = [dict(r) for r in con.execute(
        "SELECT code, qty, price, stop_pct, ts, exit_price, algo_json FROM trades "
        "WHERE exit_ts IS NULL ORDER BY id")]

    lines = []
    add = lines.append
    cache = {}
    for row in closed + opens:
        code = row["code"]
        if wanted and code not in wanted:
            continue
        if code in cache:
            continue
        try:
            cache[code] = minute_series(code)
        except Exception:  # noqa: BLE001
            cache[code] = []

    covered = [r for r in closed + opens if (not wanted or r["code"] in wanted)
               and (cache.get(r["code"]) or [])
               and cache[r["code"]][0][0] <= stamp_of(r["ts"]) <= cache[r["code"]][-1][0]]
    usable = [r for r in covered if r.get("exit_ts")]
    open_covered = [r for r in covered if not r.get("exit_ts")]
    skipped = [r for r in closed if r not in usable]
    add("EXIT-RULE REPLAY - covered: {0} closed + {1} open of {2} closed / {3} open".format(
        len(usable), len(open_covered), len(closed), len(opens)))
    if skipped:
        add("  closed but outside the minute window: " + ", ".join(
            "{0}({1})".format(r["code"], str(r["ts"])[:10]) for r in skipped))
    add("  closed rows: entry minute -> end of the session the trade really closed in")
    add("  open rows:   entry minute -> the newest minute the chart carries (mark)")
    add("  (same sessions as the trade; the rule only chooses WHEN)")

    def run(row, cfg):
        """Return ``(pnl, reason, session_minutes)`` for one row under one rule."""
        entry = float(row["price"])
        qty = float(row["qty"])
        stop, tp = levels(row, cfg)
        series = cache[row["code"]]
        days = session_stamps(series)
        end = stamp_of(row["exit_ts"])[:8] + "1530" if row.get("exit_ts") else None
        reason, price, exit_stamp = simulate(
            entry, stop, tp, cfg.get("arm", 0.055), cfg.get("trail", 0.05),
            series, stamp_of(row["ts"]), end,
            hold_sessions=int(cfg.get("hold_sessions") or 0),
            time_stop=cfg.get("time_stop"), flat_stop=cfg.get("flat_stop"),
            days=days,
            hold_minutes=int(cfg.get("hold_minutes") or 0),
            time_stop_minutes=cfg.get("time_stop_minutes"))
        return ((price - entry) * qty, reason,
                held_minutes(stamp_of(row["ts"]), exit_stamp, days))

    def table(group_label, group):
        add("")
        add("=== {0} (n={1}) ===".format(group_label, len(group)))
        add("  {0:<30} {1:>9}  {2:<9} {3:>9}  {4}".format(
            "rule", "pnl", "W/L", "avg_hold", "exits"))
        for label, cfg in rules.items():
            vals = [run(r, cfg) for r in group]
            total = sum(v[0] for v in vals)
            wins = sum(1 for v in vals if v[0] > 0)
            losses = sum(1 for v in vals if v[0] < 0)
            reasons = {}
            for v in vals:
                reasons[v[1]] = reasons.get(v[1], 0) + 1
            avg_min = sum(v[2] for v in vals) / len(vals) if vals else 0.0
            add("  {0:<30} {1:>+9,.0f}  W{2:>2}/L{3:<2}  {4:>6.0f}분  {5}".format(
                label, total, wins, losses, avg_min,
                " ".join("{0}:{1}".format(k, v) for k, v in sorted(reasons.items()))))

    table("폐쇄 거래 재생", usable)
    table("미결 포지션 (현재가 평가)", open_covered)

    if args.detail:
        add("")
        add("=== PER-TRADE PATH (pct vs entry at each session close from the entry session) ===")
        add("  {0:<8} {1:<7} {2:<13} {3:>8} {4:>8} {5:>8} {6:>8} {7:>8} {8:>8} {9:>8}".format(
            "code", "state", "entry", "s1", "s2", "s3", "s4", "s5", "max", "real"))
        for row in usable + open_covered:
            code = row["code"]
            entry = float(row["price"])
            series = cache[code]
            days = session_stamps(series)
            entry_stamp = stamp_of(row["ts"])
            sessions = [d for d in sorted(days) if d >= entry_stamp[:8]]
            closes = []
            for day in sessions[:5]:
                last = days[day][-1]
                close = [c for s, c in series if s == last]
                closes.append((close[0] / entry - 1) * 100 if close else 0.0)
            seen = [c for s, c in series if s >= entry_stamp]
            peak = (max(seen) / entry - 1) * 100 if seen else 0.0
            real = ""
            if row.get("exit_price"):
                real = "{0:+.2f}".format((float(row["exit_price"]) / entry - 1) * 100)
            add("  {0:<8} {1:<7} {2:<13} {3} {4} {5} {6} {7} {8:>+8.2f} {9:>8}".format(
                code, "closed" if row.get("exit_ts") else "open", entry_stamp[:13],
                " ".join("{0:>+8.2f}".format(x) for x in closes[:5]).ljust(44),
                "", "", "", "", peak, real))

    add("")
    add("  실제 체결 (covered 구간): pnl {0:>+9,.0f}".format(
        sum(float(r["pnl"] or 0) for r in usable)))
    add("  실제 전체: pnl {0:>+9,.0f}".format(sum(float(r["pnl"] or 0) for r in closed)))
    add("")
    add("caveats: 1-minute closes (no intrabar path); sessions only; entries/qty fixed;")
    add("         read the deltas between rules, not the absolute level (coarse prices")
    add("         do not reproduce the live trail's timing); older sessions are excluded.")
    add("         avg_hold counts only minutes with a 1-minute bar (session time).")

    text = "\n".join(lines) + "\n"
    if args.out:
        io.open(args.out, "w", encoding="utf-8").write(text)
        print("written", args.out)
    else:
        # cp949 console: write stdout as UTF-8 bytes so Korean survives.
        sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
