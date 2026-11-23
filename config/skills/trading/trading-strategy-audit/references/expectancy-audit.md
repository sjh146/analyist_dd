# Expectancy audit recipe (fillable subset, session-level, net of costs)

Use when asked "does this strategy make money?" or before relaxing a gate/threshold. Inputs are the
strategy's own replay artifacts — do not re-run the model if a per-session candidate dump exists.

## 0. Inputs

- Per-session candidate files from a screener replay (`candidates_YYYYMMDD.csv`): script, code, score,
  day-change %, and the entry/exit assumption used by the replay tool.
- Or a flat trades table with columns: `date, code, score, day_change_pct, ret_next_open_pct,
  gate_ok, r1_ok, heat_ok` (booleans as true/false). A gate-reason string column is often absent —
  derive the bucket from the boolean pair (`r1_ok`+`heat_ok` → 4 buckets).
- Fill prices come from the daily bars table (`open/high/low/close/volume/trading_value`); keep the
  replay's stated entry assumption (e.g. discovery-day close ≈ the 14:50–15:29 entry) as an explicit
  limitation in the report.

## 1. Cost model (keep it in one place, cite the file it mirrors)

```
FEE_BUY = 0.00015 ; FEE_SELL = 0.00015 ; TAX_SELL = 0.0018   # KRX retail, mirrors pnl_backtest.py
ROUND_TRIP_PCT = (FEE_BUY + FEE_SELL + TAX_SELL) * 100      # = 0.21 %p
net_pct = gross_pct - ROUND_TRIP_PCT
```

Make the rates CLI-overridable; do not silently hardcode a different convention than the backtester.

## 2. Fillability filter ladder (this is the step that flips signs)

Apply ALL of these as first-class options and report the ladder, never a single filtered number:

- `--max-day-chg <25|20|15>`: exclude candidates already up that much on the day (limit-up lock /
  unfillable at the close). Measured: no filter → +5.23 %/session; `<25` → −0.67 %/session; the effect
  is monotone, so a filter that changes the sign is a finding, not a nuisance parameter.
- `--min-value`: liquidity floor from `trading_value` (also guards the venue's own quoting limits).
- `--min-price/--max-price`: drop penny and illiquid band names.
- `--topk K`: reproduce the real selection size (position cap) instead of averaging everything.

## 3. Session-level statistics

```
per_session = group by date -> mean(net_pct of the K picks by score desc)
report: n_sessions, mean, median, positive-session share, sd, t = mean/(sd/sqrt(n)), worst session
```

Row-level means are for diagnostics only; every verdict number is session-level.

## 4. Scenario matrix

Compute the same statistics for: current gates (intersection), each gate alone, and no gate — so the
report can say *which* gate binds and what removing it would do. Report each gate's pass-session count
in the same table; a 0-session intersection is the headline, not the exclusions.

## 5. Robustness before verdict

- First half vs second half of the window: require both positive, else label "unstable".
- Parameter sweep: report combinations tried + best t (multiple-comparison risk), never just the best.
- Sanity-null: run the identical pipeline with the score shuffled within each session. If the "edge"
  survives shuffling, the pipeline has a leak; if it collapses, the number was ordering.

## 6. Swing/hold-N variant

Same recipe with entry = next session's open and exit = stop/take-profit/horizon on daily bars; the
bucketing is by the live gate (e.g. top-10 mean probability floor). Entry at the *open* is buyable in
ways a limit-up close is not, so keep the two paths' verdicts separate — they can differ in sign.

## 7. Report shape

One table (scenario × n_sessions × mean × median × positive share × t), the fillability filter used,
the cost model, the split-sample row, then one line: does a buyable, net-positive configuration exist?
If not, say "no edge in the tradeable subset" and point at the model/label lever instead of the gate.
