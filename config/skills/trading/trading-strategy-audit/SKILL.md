---
name: trading-strategy-audit
description: "Use when auditing a strategy from its execution record."
---

# trading-strategy-audit

For the class of task "look at how it actually traded and tell me what to change" — auditing an
automated strategy from its own recorded decisions and fills, then proposing concrete edits to the
selection filters, sizing and exits (does the signal discriminate, is the entry a chase, is the stop
inside the noise, what would a different threshold have done). Companion skill:
`live-order-path-safety` covers whether the order path, bookkeeping and risk gates are *truthful*;
this one covers whether the *decision rule* is any good. Read both before touching a live strategy.

## Rule 0 — the record is the evidence

The journal (decisions + trades) plus the broker's positions/marks and the venue's own high/low are
the only admissible inputs. Never tune from a narrative, a single anecdote, or memory of "it felt
like chasing". Extraction recipes (queries, ratio fields, parsing free-text entry reasons) live in
`references/execution-record-queries.md`. Reproduce the numbers before proposing anything, and quote
the measured value in the proposal.

## The four diagnostics that have each found a real defect

1. **Score/key SPREAD, not level.** Print min/max/spread of the ranking key per decision batch. A
   composite whose reference constants sit below the live distribution saturates: every candidate
   lands in a narrow band (measured 88-99, mean 93.7, within-batch spread ~3.9 points), which makes a
   minimum-score gate, an "average of top N must exceed X" gate, and "take the top N by score" all
   inert — the selection step is effectively random. Fix by normalising within the batch
   (percentile/z-score across the candidates) or moving the reference constants to the real
   distribution, then re-check the spread.
   **Normalising is not free: it makes any floor gate binary.** Once scores are within-batch
   percentiles, an "average of the top N must exceed X" gate has no resolution whenever the scored set
   *is* the traded shortlist (a self-fed candidate source): the mean of within-batch percentiles is ~50
   whatever the quality of the list (measured 84/58/50/44/40/24 -> 50.0, and the session produced zero
   entries against a floor of 55). X >= 50 then blocks every cycle and X < 50 can never fire — the gate
   carries no information, so delete it or replace it with a move-based guard (average day-move,
   ceiling, pullback), which does vary with the batch. **Clamp any auto-tuner below the same mean:** a
   tuner that tightens the floor in bounded steps (e.g. +5 per review) walks it to and past 50 within a
   couple of reviews and kills the feed with no error anywhere.
   **Before tuning the key, check that its source is alive.** A saturated or collapsed key has two families
   of cause: the scoring rule's own constants (above), and a scorer whose *upstream fit never ran* — a
   crashed trainer, a stale model artifact, a batch job that exited non-zero. The second looks like a
   healthy screener emitting a near-constant value (measured: 17 of 20 candidates on one value, all
   predictions on one side of 0.5) with nothing but a warning in the pipeline stage that trains inside
   the same run. Read that stage's log and the artifact's build time, and confirm the model was actually
   refreshed, before changing any threshold — otherwise the tuning is done against a dead scorer.
2. **Entry price against the session's own high/low.** The venue's quote response carries day
   high/low/volume; use them. A momentum list bought at the last price is a top-of-move buy *by
   construction* (measured: every entry 0.5-8.9 % below the day's high). A pullback condition
   (`last <= high * 0.97`) costs no extra request and directly removes the chase.
3. **Stop distance against the session RANGE.** A fixed percentage stop is meaningless without the
   range it must survive: a -7 % stop inside a 19-32 % daily range is inside the noise, so it trips
   on ordinary wiggle or — when the price feed lags — lets the position run far past it. Both stop
   and size want to scale with volatility (stop = max(floor, k x range); qty = risk budget / stop
   distance), which makes per-position risk uniform instead of equal-notional.
4. **Counterfactual threshold test.** Before changing any filter, compute what the candidate values
   would have done to *this* record: "daily-move ceiling X blocks N of the session's entries; their
   realised P&L was Y; the surviving set was Z". Present it as a what-if on n observations, never as
   proof. It is the difference between "12 % sounds reasonable" and "12 % would have blocked every
   entry that lost money".

## Two more, when the record supports them

- **A floor cannot catch euphoria.** Minimum thresholds only reject weak candidates; if the worst
  sessions coincide with the most extreme candidate sets (average key and average move both at the
  top of their history), add a *ceiling* gate (or halve size) for an overheated batch.
- **Count the clusters, not the positions.** Names opened in the same window from the same list are
  one bet wearing several tickers. Judge exposure by cluster (same screener, same session, same
  direction) and cap it, not by position count.

## When the filter and the selection window sample the same end

A ceiling is not symmetric with a floor. A floor rejects the tail you were not looking at; a ceiling
applied to a list *sorted by the capped metric* rejects the head — exactly the window the selection
rule samples. Take the top N of a day-move-descending list and cap day-move, and every row in the
window sits above the cap: the strategy yields **zero** candidates, all day, with no error and no log
line (measured: top-6 rows at +18 to +29 % against a 12 % ceiling, `decisions=0` for the session).
When a fed strategy produces nothing, check the pair (list sort key, filter key) before concluding the
market was quiet. The fixes are a higher ceiling, a window that samples lower in the list, or a
different sort axis — and whichever you pick, re-run the counterfactual: the ceiling was presumably
added because the names it blocks lost money.

Prefer **narrowing the selection window** over deepening the sample, because it costs the same number
of requests: with `pct_to` set to the ceiling the page opened at +14.5 %, at 12 % it opened at
+11.9 %, while the wide window opened at +18 % and above — the sampled rows finally land inside the
band you are willing to buy. Deepening the sample (`top N` 6 -> 8) only helps when the extra rows are
also inside that band, and every extra row costs one quote request per cycle, so check both numbers
against the live table before raising the depth.

Detect it from live data rather than from the record alone: pull the ranking rows, quote their codes,
compute the move yourself (`pct = diff / (current - diff) * 100`, normalising the sign code — sign
fields arrive as ASCII ints, `50` = `'2'` = up) and tabulate which of the filters kills each row.
`scripts/rank_probe.py` runs that measurement directly (window, sort and depth are flags; it prints
rank / code / move / distance below the high / volume and writes the table as UTF-8) — run it before
choosing a ceiling, a window bound or a sample depth.

## Audit the backtest before the rule: is the expectancy FILLABLE?

A counterfactual that assumes a fill you could not have got is not evidence — it can flip the sign of
the whole strategy. Run this pass before proposing any gate/threshold change (recipe and tool
commands: `references/expectancy-audit.md`).

- **Filter for fillability, then compute expectancy.** Names locked at the venue's daily limit (upper
  limit = no sellers) cannot be bought at the close, and gap-and-run names cannot be bought at the
  assumed open. Measured on 87 sessions / 1,736 candidates: the unfiltered replay gave +5.23 %/session
  (t = +8.8) with the headline "the day-move ceiling is costing us money"; excluding candidates already
  up ~25 %+ on the day (396 of 1,736) turned the same rule into −0.67 %/session (t = −2.8), and every
  bucket of the gate comparison flipped sign. Report the *buyable* subset's number and name the filter
  (move cap, liquidity floor, price band) in the same sentence.
- **Aggregate per session, not per row.** One session contributes dozens of correlated candidates, so a
  row-level mean inflates n by ~20x and manufactures significance. Take the per-session mean first,
  then mean / median / positive-session share / t over sessions.
- **Split the window in half and require both halves positive** before calling an edge real; one window
  in one regime is not a verdict. If you swept parameters, report combinations tried with the best t,
  not just the winner.
- **Check that the gate you want to relax ever opens.** Count sessions where each gate passed before
  touching its threshold: measured 38/87 for the score floor, 3/87 for the move ceiling, **0/87 for the
  intersection** — a gate whose intersection never opens is a closed door, not a mis-tuned filter, and
  the fix is a definition that lives in the distribution (percentile), not a smaller constant.
- **Price round-trip costs in** (~0.21 %p for KRX retail: buy fee + sell fee + transaction tax). A path
  that is +1.8 %/row gross can be −0.2 %/row net once the unfillable tail is gone.
- **Check what the ranking key actually ranks.** Print the day's-move distribution for the top N by
  score before trusting the ordering: measured median **+29.9 %** with 82 % of picks at/above +25 % —
  the key was a proxy for "already went up today", so the head of the list was unbuyable by
  construction. Fix selection *inside a fillable universe* first, then re-measure; relaxing the gate on
  an unfillable ordering just opens a losing path.
- Empty-path symptom: when a fed strategy yields zero entries for a long stretch, check the pair
  (list sort key, gate key) and each gate's pass counts before blaming the market — a never-opening
  intersection looks exactly like a quiet market in every log.
- **Sweep it, don't spot-check it.** After the fillability filter, the honest question is not "is
  this bucket positive" but "does *any* fillable configuration pay": sweep (selection depth K x move
  cap x liquidity floor x exit rule) and report combinations tried alongside the best t. Measured over
  240 combinations: exactly one met (net>0, t>=2, worst session bounded) and it was **unfillable**;
  **0 of 180 fillable combinations** had a positive mean, best fillable t = **-0.42** - i.e. below
  what pure noise reaches at N=240 (E[max t] ~ sqrt(2 ln N) ~ 3.3), so no multiplicity correction is
  even needed to call it noise. Split the window in half and require both halves positive: 0 fillable
  combinations were, so the result is not a regime flip. Also sweep the exit: lengthening the hold
  (next close, T+2 open) made it *worse* (worst session -16 to -34 %), which is the evidence for
  keeping the current next-open exit rather than a reason to change it.
- **When one path shows no edge, measure the sibling path before concluding the model is worthless.**
  The same signal can be unprofitable at one horizon and positive at another. Measured: the
  close-to-next-open path was negative after fillability filtering, while the longer-horizon path's
  gated subset averaged +4.6 % with a 73 % win rate (n=11) against -1.8 % for its rejected subset
  (n=9) - direction, not a verdict at that n, but it identifies where to spend the next measurement.
  Report it with n and the "no edge yet" alternative in the same sentence.
- **Standing check: compare the live score distribution against each gate threshold, per date.** A
  model whose scores collapse (one date's top-10 mean 0.25 against a 0.58 floor, versus 0.74-0.79 on
  healthy dates) closes the path silently; the same table tells you the gate is fine and the *scorer*
  is broken. Run it before tuning any threshold.

## Sweep the exit width against the cost floor before accepting it

"Make the stop/target tight (1-2 %) and trade intraday" is a *cost* change, not an execution detail.
Quantify it before coding it (breakeven table below; simulate the width on your own fills before proposing):

- **Breakeven win rate for a symmetric line** with round-trip cost c (KRX retail ~0.21 %):
  `p* = (s + c) / (2s)` -> +-1 % needs **60.5 %**, +-1.5 % 57.0 %, +-2 % 55.2 %, +-7 % 51.5 %.
  A 1 % target nets 0.79 % and a 1 % stop costs 1.21 %, so **tightening raises the required win
  rate** — say that number in the same sentence as the proposal.
- **First measure the no-stop baseline on the same signal.** If next-open-to-close is already
  negative (measured -1.33 %/trade, 36 % win on 11.3k momentum signals), no exit width flips the
  sign: 1 %/1 % worst -0.99, 1 %/2 % mid -0.15, 7 %/7 % mid -0.82. The exit sets the *size* of the
  loss; the entry signal decides profit. Report an exit-only change as loss reduction, never as
  edge.
- **Daily OHLC has no intraday ordering**, so narrow-line conclusions are dominated by the
  assumption: with +-1 % both barriers were touched on 88.9 % of days (vs 25.8 % at +-7 %). Always
  report worst (stop first) / best (target first) / mid (50:50) and quote only the mid as a single
  number. Partial minute data (e.g. closing 30 minutes only) is not a substitute for a replay.
- **Measure the ambiguous share before you analyse anything.** Count trades whose worst and best
  outcomes differ (barriers both touched, multi-session holds and trailing included). Same-day +-1 %
  gave 88.9 %; a capital-constrained run with 1-5 session holds and trailing gave **95.9 %**, and the
  sign flipped between the extremes (−0.74 %/trade stop-first vs +0.71 %/trade target-first). At that
  level the table cannot decide anything — the next artifact must be a minute-path replay, and any
  number quoted from the daily table is a band, not a result.
- **Replay barriers on minute closes, not minute bars.** Venue minute data often arrives with only
  close + cumulative volume (no OHLC). Treat the first minute close beyond a barrier as the fill,
  choose stop-first when both are crossed inside the same minute, and count those collisions — then
  state the coverage (`skipped_no_minute / total`) next to the expectancy so a thin replay is visible.
- **Portfolio constraints belong in the replay, not in the caveats.** Integer `floor(budget/price)`
  shares, per-stock cap (equity/3), max concurrent positions, daily entry cap and a price band change
  which trades exist at all: measured **14,555 signals → 460 fillable** (11,692 rejected on slots,
  2,397 on the price band). Report the rejected counts by reason; a signal-level expectancy from a
  universe you could not have bought is a different strategy.
- **Max drawdown must come from a capital curve.** Summing returns or compounding per trade over
  thousands of rows produced impossible values (5,873 %). Roll fixed capital with the per-trade
  weight, take peak-to-trough on that equity curve, and if the definition cannot be made honest yet,
  emit `null` with the reason instead of a number.
- **A tight stop's cost shows up as whipsaw, not just as losses**: measure the share of trades whose
  low breached the stop yet closed above entry (26.8 % at +-1 %, 2.0 % at +-7 %) — that is the
  population the tight line cuts before it can recover.

## Entry filters: test as-of, and split them by role

A filter that would have blocked a known loss is not evidence; a filter is evidence only if the
blocked cohort's realised return is worse than the surviving cohort's, measured on the same signal set.

- **As-of discipline is the whole test.** Only data available *before* the entry may be used, so
  financials/disclosures get `report_date <= entry_date` (or a filing lookback window). Measured on
  11.3k signals: `ROE < 0` blocked 19-24 % of signals whose average was -1.43 % vs -1.29 % for the
  survivors (**+0.14 %p** — the only filter that earned its place), market-warning filings (관리종목/
  투자주의/불성실공시) +0.06 %p, and a dilution-filing filter (유상증자/전환사채) was **negative**
  (-0.147 %p): those names did not underperform on average.
- **Split disclosure filters by timing role.** A dilution/CB filing that arrives *after* entry cannot
  block it — it belongs on the exit path as an immediate-liquidation trigger. Check the timestamp of
  the filing against the entry before claiming a filter would have prevented a loss (the case that
  motivated the filter usually had the filing one week *after* the buy).
- **Report the blocked share with the improvement.** A filter that improves the average by 0.06 %p
  while deleting a quarter of the signal stream may not be worth the sample loss; state both numbers
  and let the owner choose.

## Expectancy first, and design the reward side (asymmetric R)

The owner's standing criterion: trading is not prediction but **the same probability repeated hundreds of
times**, so judge with expectancy, never with win rate alone — a 40 % win rate is fine if losses stay small
and winners large. Report `기대값 = p·평균이익 − (1−p)·평균손실 − 비용` together with the realised win rate,
avg win / avg loss (**R**), profit factor and max drawdown. Measured on the live book (11 closed trades):
win rate 22.2 %, avg win 1,028 vs avg loss 5,075 → **R = 0.203 (inverted)**, expectancy −2,943/trade,
profit factor 0.058 — a high-level failure of the *reward* side, not of the win rate.

- **Default to asymmetric exits.** Symmetric bands (7 %/7 %, 1.5 %/2.5 %) are controls, not the target:
  small stop, long target (e.g. stop 1.5 x ATR14, target 3.0 x ATR14), stop clamped to `max(0.5%)-min(8%)`.
  With breakeven `p* = (loss + c)/(loss + win)` at c = 0.21 %, R:R 3 needs 43.8 % and R:R 4 needs 40.0 %,
  versus 60.5 % for a symmetric ±1 % — the reward side is where the requirement drops.
- **Narrowing the stop is still only loss reduction** (see the exit-width section): saying "go to 1 % stops"
  without raising R or filtering entries buys nothing. Propose the pair together or say why not.
- **Edge = a stack of agreeing factors, not one indicator.** A workable 0-7 score, all computable from the
  entry-day bar and earlier: trend direction (close > MA20 and MA5 > MA20) · trend slope (MA20 5 sessions) ·
  support/resistance breakout (within 2 % of the 20-day high) · candle quality (body ≥ half the range and
  close in the top third) · volume confirmation (2x the 20-day average) · volatility fit (ATR 1-6 % of price) ·
  overheat avoidance (day ≤ 12 %, 5-day ≤ 25 %). Ship each factor as its own boolean plus the integer score
  so the record shows *why* a trade passed, and gate on the score rather than on any single factor.
- **Adoption gate for any threshold tuned this way**: pick the rule on the first half of the window, apply it
  to the second half **without re-tuning**, and require expectancy > +0.10 %/trade, n ≥ 300 and realised win
  rate ≥ `p*`. If the holdout does not clear it, ship nothing — and never move a threshold after seeing the
  result (report it as measured, including zeros).
- **Sweep the composite's threshold before shipping it as a gate.** Measured on the 0-7 confluence score:
  raising the cut from 0 to 3/4/5/6/7 moved expectancy *down* (−0.05 → −0.10 %/trade) while the live list
  carried mostly 1-2 points — a gate at 5 blocks nearly everything and adds no EV. A flat sweep means the
  composite is not the binding constraint: report "threshold does not help" and go back to the entry
  definition, rather than shipping the gate because it sounds like discipline.

## Reporting discipline

- State **n** with every finding. A single session of a handful of entries shows direction, not
  edge; say so in the same sentence as the number.
- Separate *structural* findings (a saturated score, a top-of-range entry, a stop inside the range
  are mechanical facts about the rule) from *statistical* ones (which names won). Act on the
  structural ones first; they need no significance test.
- If the record shows no evidence of edge yet (e.g. a couple of closed trades, all losses), say that
  plainly and propose the smallest-risk way to keep collecting data, rather than tuning thresholds to
  fit two samples.
- Report in the user's language, lead with the defect and its measured number, then the proposed
  change with its counterfactual, then the caveat. Ask which changes to apply instead of silently
  rewriting a selection algorithm — and name the config key for each so it stays tunable.
- After a change, the evidence requirement moves to the next review: the same diagnostics re-run on
  the new record are how a threshold gets confirmed or relaxed.

## Before any learned model, count the labeled rows

"Should we fit a model on this?" is answered by counting first. Below roughly 20 closed trades a
sequence model (thousands of parameters) can only memorise that period's market direction, and with
no holdout its edge cannot be told apart from overfitting — say the number instead of agreeing to it.
The order is **rules → tabular model (GBM / logistic) → sequence model**: if a tabular model shows no
walk-forward edge on the same features, a sequence model will not either. Non-stationarity,
look-ahead and explainability are data-design problems, not model-choice ones — the entry-time market
state has to be in the features or the label is self-confirming (the model relearns the market
direction of the sample window). Check the logging before proposing anything: fills alone carry
features (per-fill `algo_json`) while the decision log may keep only gate text and score for the
rejected candidates, which leaves "would the rejected candidate have risen?" unanswerable — attach
candidate features plus a forward-return label to **every** evaluated candidate, and that table is
the prerequisite for any model. Live inference per candidate also adds cycle latency, which is part
of the strategy's behaviour, and sequence models that genuinely earn their place belong on a separate
news/tick collection pipeline rather than in the live decision cycle.
