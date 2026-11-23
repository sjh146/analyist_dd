# Exit levels and sizing — what determines the stop, the take-profit, the quantity

Answers "이 선은 무엇으로 정해지나" and "스탑로스·켈리 적용된 거야" from the code, with the numbers for the
positions actually held. Plan-time logic is `trader_core/engine.py`, runtime is `trader_core/monitor.py`,
formulas are `trader_core/risk.py`, defaults are `trader_core/config.py`. The values a position is really
judged by are on its journal row (`trades.stop_pct`, `trades.tp_pct`, `trades.hwm_price`) and in
`trades.algo_json` — read them, do not recompute by hand.

## The chain

| line | rule | where |
|---|---|---|
| stop | `min(max(floor, factor × day_range), cap)` | `risk.volatility_stop_pct` |
| take-profit | `stop × (take_profit_pct / stop_loss_pct)` | `engine` |
| trail | armed at `+trailing_arm_pct`, exits at `-trailing_stop_pct` from the high-water mark | `monitor._trailing_verdict` |
| time exit | profile: `stop_tp` / `next_open` / `same_day_close`, plus the shelf-life ladder (`max_hold_sessions`, `flat_stop_*`, `time_stop_*`) | `profiles.py` + `monitor._add_time_exits` |
| qty | `min(kelly_qty, risk_qty)` | `risk.sized_qty` |

- `day_range = (day_high − day_low) / day_low × 100`, taken from the live quote at plan time. Missing or
  zero range falls back to the floor (fail open) and the record says so: `algo_json.stop.model` is
  `"fixed"` instead of `"vol-range"`. A held name whose model is `"fixed"` is running an older, usually
  7%, stop — say so when reporting an imminent exit, because the line about to fire is the old setting,
  not the model currently in use. For a 20-30% day range a 7% stop sits inside the daily noise.
- Defaults: floor 0.07, factor 0.5, cap 0.20 → a 22.9% day range yields an 11.5% stop.
- Take-profit ratio = `take_profit_pct / stop_loss_pct`; with both 0.07 the reward:risk is exactly 1.0 and
  **take-profit == stop**. Raising only `take_profit_pct` raises the ratio for every new entry.
- Verdict order per heartbeat: fixed stop → take-profit → trailing (a tie resolves to the stop).
- The trailing peak lives on the journal row (`hwm_price`), so a restart does not reset it, and it does not
  arm at all until the position is up `trailing_arm_pct`.

### The arm/trail pair is one knob, not two

An armed trade's exit line is `peak × (1 − trailing_stop_pct)`, so the trail must sit *inside* the arm or
an exit rule built to protect a profit sells **below the entry**: at arm 3% / trail 5% the trigger for a
+3% peak is -2.15%, and a measured batch of trailing exits closed -3.0 / -1.6 / -2.1 / -2.0 / +0.3 %
(mean -1.7 %) while their fixed stops allowed -8..-15 %. Keep `arm >= trail` — worst armed exit is then
`(1+arm)(1−trail) − 1` (=-0.09 % at 3%/3%), and for a strictly positive lock `arm >= trail / (1 − trail)`
(a 3% trail wants 3.09% arm). Replaying those same peaks through 3%/3% moves each exit to about +0.8%
(+2.5 %p per trade). Pin it with a test asserting the worst armed exit is at/above break-even.

### A profile can override the global arm/trail — wire EVERY exit path

The global pair is one setting for all strategies; a fast strategy (30-minute-to-hours scalp) needs a much
tighter pair than a multi-session swing one. Put `trailing_arm_pct` / `trailing_stop_pct` on the profile
with `0` meaning "use the global", and resolve it **inside each exit path that computes a trail**: the
cycle monitor and the sub-second tick watch are separate functions, and patching only the monitor leaves
the fast path on the stale global pair — the exit that actually fires can then be the loose one. Tests pin
both: a profile-armed row trails at the profile width, an unconfigured strategy keeps the global pair.
The fixed take-profit can stay as a ceiling above the trail; the trail simply fires first in practice.

### Measure reachability before widening the take-profit

`trades.hwm_price` is the recorded peak, so "would a 2× stop target ever have been hit?" is answerable from
the closed rows: compare `hwm / entry − 1` against `stop_pct × ratio`. When the peak rarely reaches the
target (measured: hwm ≥ stop×2 in 1 of 20 closed trades, median peak +3.3 %), a wider take-profit lowers
the win rate instead of raising the payoff — the lever is entry selection or the trail, not the target.
Both replays are pure arithmetic over the journal; never deploy an exit change that has not been replayed,
and never move stop/target widths and the trail in the same change (one knob at a time, then re-measure).

## Time exits are a second axis: the shelf-life ladder

A `stop_tp` position (swing, or a self-generated momentum list) can also be closed by *time*, which is a
separate axis from the price lines: `max_hold_sessions` (hard cap — close at the end of session N),
`flat_stop_sessions` + `flat_stop_band` (release dead money when `|gain| <= band` at session N) and
`time_stop_sessions` + `time_stop_gain` (cut a trade that never worked). All live on the screener profile,
all `0` = off, all evaluated in `profiles.ScreenerProfile.time_rules_due`, and all fire **only inside the
profile's `close_exit_window`** — the same rules evaluated mid-morning report "hold" for every position.
Sessions count the entry session as `1` (2 = "exit at the next session's close") and skip weekends, so an
exchange holiday can make them fire one session early; say that when reporting a date.

Two asymmetries worth stating in any report about them:

- Rules that need a price **fail open**: no quote or no entry price -> flat / time-stop cannot fire, while
  the hard `max_hold` cap (no price input) still can. A missing quote must never force a sale.
- The ladder is **orthogonal to the stop** and comes from the profile, not from the row — so a position
  whose row predates the ladder still gets it, unlike the stop/tp levels frozen at entry.

Before deploying one, print its verdict for the book that is actually held (read-only, one bridge GET):
`profile_for(row["screener"]).time_rules_due(row["ts"], now, gain)` with `gain = mark / entry − 1` from
`/positions` `eval_amount ÷ qty`. That proves the wiring on real rows and lets you name the positions the
next window will close. Evaluate **twice** — once at the real `now` and once at a time inside the close
window — because outside the window every row reads "hold" and the first result is not the answer.

### A hard minute cap cuts winners — condition the extension on profit AND the market's move

`max_hold_minutes` is the one time rule with **no price input**, so it fires on a position that is up and
still trending; measured, a 90-minute cap closed a name at +0.16 % that ran to +1.4 % later the same
session. Never let a bare clock own the exit: extend the cap when (a) the position is in profit and
(b) the benchmark index is **above its level at entry**. Record that level at plan time
(`algo_json.market_index = {KOSDAQ: ..., KOSPI: ...}`) — it cannot be reconstructed afterwards — fill the
current level every cycle from the same published payload the regime gate uses, compare KOSDAQ first
(KOSPI as fallback), and when the level is missing on either side **do not extend** (fail-safe: the old
cap stays). Keep a hard upper bound (a few hours, well inside the profile's close window) and exit on the
next pass as soon as gain falls back to 0 or below — that is a break-even guard, not a new rule. Test all
four quadrants: profit+index-up extends, profit+index-down honours the cap, flat/losing honours the cap,
and past the extended bound honours it too.

## A config change does not move an open position

Levels are frozen onto the row at entry, and the monitor reads the row **before** falling back to the
config defaults. To move levels on what is already held, edit the rows (`exit_ts IS NULL` only — closed
rows are audited history):

```
python tools\set_open_tp.py --sl 0.07 --tp 0.105 --codes 950250      # add --dry-run first
```

Config-default changes reach new entries only, and only after a loop restart: a running process keeps the
config it started with.

### Resolve arm/trail from the config, never hardcode the pair in a report

The pair is tuned from measured exits, so a tool that prints an exit line must read it from the code
rather than carry its own constants: a stale pair misstates the trail line and silently re-orders the
"closest to its exit" ranking while looking authoritative (measured: a snapshot tool holding 3%/3%
reported a trailed holding 1.9 % from its line where the live 5.5%/5% pair put it 4.1 % away — the one
number the operator would act on). Treat the tool's output like a deployment: change the knob, re-run the
tool, and confirm the printed line moved.  A script under `tools/` needs the repo root prepended to
`sys.path` before it can import the project config (the script's own directory is `sys.path[0]`, not the
cwd), so an import written that way fails quietly and execution continues on fallback constants — print
that fallback ("using arm/trail X/Y") so a reader can tell a measured line from an assumed one, and keep
the fallback loud rather than silent.

## Entry-count caps live in two places, and one can silently bind below the other

A daily entry limit is usually enforced twice: the engine's own `daily_max_trades` and the runner's
`r2_max_entries_per_day`. Either one alone binds, and the log then names the loser. When the owner asks to
change the cap, change **both** (and the tests that pin the defaults) or the effective limit is the
smaller one — measured: raising the engine cap to 10 changed nothing because the runner still refused at
3, and the reason line quoted the runner's number. A regime- or path-specific sub-cap is a third value
that can sit below both; print the gate's *effective* max from the live log (`today 3 < max 10`) instead
of assuming the config default is what ran, and prefer `0 = no extra cap` over a magic second limit.

## The price an exit is judged on

Each heartbeat pulls `/positions` + `/quotes` and cross-checks the quote against the broker's own mark
(`eval_amount / qty`, same payload). Beyond `quote_divergence_pct` (0.01) the **lower** price wins for a
long — stops still fire, take-profits do not fire early. No quote → HOLD, never a fabricated price.
Exits are market orders routed straight past the entry gates.

## Sizing — the same lines decide the quantity

```
b = avg_win / avg_loss               (from the live priors)
f* = max(0, p − (1−p)/b)             (clamped at zero: a negative edge is not an error)
f_eff = min(f* × kelly_fraction, kelly_cap, max_position_pct)     # 0.25, 0.25, 0.20
kelly_qty = f_eff × equity ÷ price
risk_qty  = equity × risk_per_trade_pct ÷ (price × stop_pct)      # 0.005
qty = min(kelly_qty, risk_qty)       (+ max_order_amount and cash)
```

`algo_json.sizing` records `{model: "kelly+risk-parity", kelly_f, kelly_qty, risk_qty, final_qty,
equity}`. `final_qty == kelly_qty` means Kelly bound the size; `final_qty == risk_qty` means the stop width
did (a wider volatility stop shrinks the position). That field is the answer to "왜 이렇게 조금 샀나" —
Kelly at p=0.53, b=1 gives f* = 0.06, ×0.25 = 1.5% of equity per name, which is why the sizes look small
relative to the risk budget.

`qty == 0` surfaces as the gate `balance_position_amount: size rounds to zero shares (Kelly or risk budget
vs stop X%)`: a candidate that cleared every filter and could not be sized at all.

Break-even win rate at which `f* = 0`: **50% at b=1, 40% at b=1.5, 33% at b=2**. A realized win rate under
that line means negative expectancy at the current payoff — fix the payoff, and check whether the trailing
stop is quietly capping realized wins below the take-profit target before crediting a raised target with
the improvement.

## Knobs (`trader_core/config.py`)

| knob | default | decides |
|---|---|---|
| `stop_loss_pct` | 0.07 | stop floor, and the stop used when the day range is unknown |
| `stop_range_factor` | 0.5 | stop as a fraction of the day range |
| `max_stop_pct` | 0.20 | stop ceiling |
| `take_profit_pct` | 0.07 | take-profit = stop × this ÷ `stop_loss_pct` |
| `trailing_arm_pct` / `trailing_stop_pct` | 0.055 / 0.05 | when the trail arms / give-back allowed from the peak — keep `arm >= trail`; tuned from measured exits, so read the live value instead of trusting a copy |
| `daily_max_trades` | profile/runner-dependent | daily entry cap — must match the runner's own counter |
| `risk_per_trade_pct` | 0.005 | KRW at risk per position (equal-risk sizing) |
| `kelly_fraction` / `kelly_cap` | 0.25 / 0.25 | Kelly scaling and ceiling |
| `max_position_pct` | 0.20 | per-name ceiling as a fraction of equity |
| `quote_divergence_pct` | 0.01 | allowed quote-vs-mark divergence before the lower price wins |

`tools/review.py` self-tuning touches the rank filters, the R1 bar and the Kelly priors only — it never
moves stop/take-profit widths, so a line that should change has to be changed through the knobs above or
the per-row tool.
