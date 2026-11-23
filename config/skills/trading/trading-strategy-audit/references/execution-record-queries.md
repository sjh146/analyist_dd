# Extracting the execution record

Concrete recipes for pulling the evidence behind a strategy audit. Table/field names are from the
trader-agent journal; the *shape* (a decisions table with serialised gates, a trades table with
free-text reasons) generalises to any journal-based strategy.

## 0. Reading the DB while the writer is running

The trading process holds the journal open on the other side of the mount and the DB runs in WAL mode,
so the newest rows (today's fills, today's exits) live in the `-wal` sidecar until a checkpoint. Copy
the DB *together with* its sidecars into a scratch directory and query the copy:

```bash
cp journal/x.sqlite3 /tmp/j.db
cp journal/x.sqlite3-wal /tmp/j.db-wal 2>/dev/null
cp journal/x.sqlite3-shm /tmp/j.db-shm 2>/dev/null
```

- Copying the main file alone yields a **silently stale** snapshot: a query for the day's exits returns
  zero rows and reads as "nothing traded today" while the running loop's own log shows the exits.
  Cross-check any "no rows" result against the process log before believing it.
- Reading in place can raise `disk I/O error` while another process writes over a drvfs/9p mount —
  copy then read, never read in place.
- The rule generalises to any SQLite the strategy writes: the sidecar files are part of the database.

## 1. Decisions — the funnel and the key spread

Each decision row carries the ranking key plus a serialised list of gate results, so both "where do
candidates die" and "does the key discriminate" come from one query.

```python
rows = conn.execute(
    "SELECT ts, screener, code, score, gates_json, plan_json, reason "
    "FROM decisions WHERE ts >= ? ORDER BY id", (day_start,)).fetchall()
```

- Gate results are objects with `gate`, `ok`, `detail` — count by `gate` where `ok` is false to get
  the funnel. A gate that never blocks anything is either mis-tuned or dead.
- `plan_json is not None` means the engine produced an order plan; comparing that against what the
  loop actually placed separates *engine* rejections from *loop* budget rejections.
- **Key spread per batch**: group by `ts`, print `min`, `max`, `max-min`. If the spread is a few
  points and the level is near the top of the range, the score is saturated and any threshold on it
  is inert. Also print the score distribution over the day (min/mean/max, n) — a mean pinned near
  95 with a 10-point range means the composite is a constant, not a ranking.

## 2. Trades — what was bought and what the filters were

Entry filters often survive only as free text in `entry_reason` (e.g. "5th +27.50% volume
1,920,258"). Parse it once, keep the numbers, and join them to the outcome:

```python
m = re.search(r"(\d+)\uc704 \+\s*([0-9.]+)% \uac70\ub798\ub7c9 ([\d,]+)", reason)
rank, pct, volume = int(m.group(1)), float(m.group(2)), int(m.group(3).replace(",", ""))
```

Then build the attribute-vs-outcome table (rank, move, volume, key → P&L, return %) and split it into
winners and losers. With small n, read the *averages per attribute* rather than fitting anything:
"losers averaged +20.4 % on the day vs +18.0 % for winners" is the honest form of a weak signal.

For marks, prefer the broker's own figures: journal rows exclude fees, `positions` carries
`avg_price` including them and `eval_amount / qty` as the current mark — the two differ by exactly the
fee drag, so state which one the table uses.

## 3. Session range — the chase and the stop-vs-noise checks

One quote request per code returns the day's `high`, `low`, `open`, `volume`, `amount` alongside
`current`, which is everything needed for both diagnostics:

```
range_pct   = (high - low) / low * 100          # how much noise the session contained
chasiness   = (entry - high) / high * 100       # <= 0 means bought at/below the day's high
stop_ratio  = stop_pct / range_pct * 100        # small => the stop sits inside the noise
```

Report as a table (code, entry, low, high, close, range %, entry-vs-high %, stop as % of range) — the
pattern is visible instantly and needs no statistics.

## 4. Counterfactual on a threshold

For a candidate ceiling/floor value, apply it to the parsed attributes and re-sum the outcomes:

```python
for ceiling in (8.0, 12.0, 15.0, 20.0):
    blocked = [r for r in rows if r.pct > ceiling]
    kept    = [r for r in rows if r.pct <= ceiling]
    # report: n_blocked, kept P&L, blocked P&L
```

Always print both sides — the P&L of the survivors *and* of the names the threshold would have
blocked. A ceiling that blocks the losers and nothing else is a strong hint; a ceiling that blocks the
best names too is a cost, and both numbers belong in the report.

## 5. Presenting it

- One table per diagnostic, each with the n it was computed from.
- Rank the proposals by (expected impact, implementation cost) and name the config key for each.
- Keep the raw extraction script out of the report; quote the resulting numbers.
