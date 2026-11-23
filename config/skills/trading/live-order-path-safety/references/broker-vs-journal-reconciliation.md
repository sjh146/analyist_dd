# Reconciling broker state against local journal rows

Run this whenever a figure looks economically impossible (a loss larger than the stop implied), after
any disconnect, and before restarting an unattended loop.

## 1. Freeze, then read both sides

Stop the loop before reading anything (a running cycle writes new rows while you inspect). Positions
are left untouched by stopping. Then pull broker truth and local truth:

- broker: `positions`, `orders` (open orders carry a filled quantity + cancelable quantity), `balance`
- local: open trades, closed trades in the window, and the decision rows for the cycle timestamps

Compare per instrument:

| broker position | broker open order | local row | meaning |
|---|---|---|---|
| present | — | open | consistent — nothing to fix |
| absent | present, unfilled | open | accepted but **not filled**: not a position. Do not size new buys against it; cancel or wait |
| absent | absent | closed | the exit did fill (or the entry never did) — verify the exit price |
| present | — | closed | the exit did **not** fill: reopen the row |

**Mid-session variant — the loop keeps running.** Do not stop the loop just to *look*: the same three
lists (open rows, `positions`, working `orders`) read fine while it trades, and together they answer both
"is the journal truthful right now" and "how many concurrent slots are really free". This is the pass
that catches the unfilled-entry class *before* its cost lands, because the phantom row is consuming a
book slot from the moment it is written. Read the journal from the side that owns the file (a same-side
interpreter, or a copy taken together with its `-wal`/`-shm`), and never edit it from the other side.
On `local open + broker flat + order still working`, the choice is: cancel the order and close the row at
the entry price now (frees the slot immediately), or leave it to the day-close pass (the slot stays blocked
until the close and the day's entry counter stays inflated by one). Say which one you chose and what it
costs; never quietly rewrite a live journal. Run the check **after** the cycle that wrote the newest rows,
not before — a row written minutes ago is exactly the case it exists to find. `scripts/status_snapshot.py`
in this skill prints all three lists side by side plus each holding's distance to its exit line.

## 2. Correct surgically, with a cited source

Prefer a targeted update over delete+insert, and keep the original order response payload as the
evidence trail:

- wrong exit price (market order recorded as 0): set `exit_price` from a **recorded** quote for that
timestamp — e.g. the decision row written by the same cycle — and recompute `pnl = (exit - entry) *
qty`. Never substitute an estimate you cannot cite.
- exit never filled: clear `exit_ts`, `exit_price`, `exit_reason`, `pnl` so the position is tracked
  again and the risk gates see it.

## 2b. Deliberate out-of-band exit while the loop runs (owner-approved)

For a position the loop will not close on its own — a window-gated exit whose window was missed, or a
holding the strategy intends to keep — the owner can ask for a manual close. Every step is evidence, not
an action whose success you assume:

1. Pre-flight in one pass: bridge `health` + `balance` + `positions` (record each `sellable_qty`) +
   `orders` (must be empty), plus the loop's own state (`halt`, `daily.entries`). Read the profile's exit
   mode/window from the code, not from memory: outside the window the loop is not going to place this exit.
2. Submit at market with the position's own sellable quantity
   (`POST /order {"side":"sell","code":"<code>","qty":<n>,"order_type":"market"}`). The echo is **not**
   a fill: a market order returns `price: 0`, and the order-number field is frequently `0`/wrong.
3. Confirm at the broker: the codes are gone from `positions` **and** absent from `orders`. Accepted +
   still working + position still held is `working`, not filled.
4. Take the fill price from the broker's own **executed-basis** query (per-code average sell price), never
   from the live quote and never from the echo. The live quote is not a fill price; booking it fabricates
   realised P&L in one direction or the other.
5. Close the journal rows at that price — the reconciler's explicit price map outranks its quote fallback
   (`--apply --fix-exit <CODE>=<broker average sell price>`, repeatable, one per code). Verify: zero open
   rows, and each row's `pnl == (exit - entry) * qty` recomputed by hand, not read off the tool's line.
6. Export the fills file the producing/analysis side consumes, then read it back: such tools often write
   relative to the **trading app's** working directory, so confirm where the artifact landed instead of
   assuming it reached the repo the consumer reads.

State to the owner: the exit frees the concurrent slots mid-session (the later entry window can fill
again), the loop's monitor just logs "no open positions" so no halt or restart is needed, and the fees for
these rows stay unresolved per §5 — the reported figure is gross.

## 3. Verify before restarting

Re-run the performance/self-tuning step in read-only mode to see corrected statistics, check the
loop's status output, then start the loop and confirm it actually cycles: the state file's
`last_cycle` timestamp must advance by roughly the configured interval. A dedicated cycle log is not
proof of life when stdout is buffered — the state file and the broker's own snapshot are.

Once the day is marked closed the loop exits on its own, and a subsequent single-cycle run reports
that instead of exercising anything. To check post-close behaviour, drive the monitor/sizing code
directly from a small script rather than through the loop CLI.

## 4. Automating the pass at the day close

A manual reconcile is a step that gets skipped, and one skipped class of drift blocks the next session
silently: an order the submit response called filled that the broker never filled leaves a row holding a
concurrent-position slot *and* the per-code "already entered today" guard, so a name that was never
bought is refused all day. Hang the pass off the loop's day-close path, with these rules:

- **Snapshot the working orders BEFORE cancelling them.** The close routine usually cancels every
  outstanding order first; once cancelled, the evidence that nothing filled is gone and the row
  degrades into the ambiguous "local open, broker flat" class. Order it: read `orders` → cancel →
  reconcile against that pre-cancel snapshot.
- **Auto-apply only the classes where the broker is the authority**: order never filled → close at the
  entry price with zero P&L; partial fill → take the broker's quantity and price; broker-only position →
  adopt at the broker's average; a closed row the broker still holds → reopen.
- **Report, never auto-close, "local open + broker flat + no working order".** Its true exit is a broker
  fill price and the live quote is not one, so closing on the quote fabricates a P&L in one direction or
  the other. Log the code, quantity, the quote as a *hint*, and the exact repair command
  (`--apply --fix-exit CODE=<broker average sell price>`), and leave the row open. Give the reconciler an
  explicit switch for this (e.g. `apply_flat_close=False`) so the interactive `--apply` keeps its old
  meaning; `--apply` on the tool and `--apply` inside the loop are different risk decisions.
- **Quote the local-open codes too, not only the held ones.** A flat-broker row has no position to
  supply a price, so a reconciler that quotes only broker positions cannot classify it at all; one
  batched quote request covers both sets.
- **Write the verdict where the next session looks**: a line per finding in the cycle log, a compact
  per-code summary into the day's state, and into the close notification — with applied classes and
  reported-only classes kept in separate keys, so "handled" and "needs a human" cannot be confused.
  Wrap the whole pass so a bridge failure reports and the day still closes.
- **It runs once, off the day-close latch.** A restart after the close does not re-run it (the loop
  exits immediately when the day is latched closed), so verify the first live run from the close
  cycle's own log lines — do not start the loop in the evening and assume the pass ran.

## Known gaps to check for in any live order path

- No fill confirmation at submit time: entries recorded from the submit response alone make partially
  filled and unfilled orders look identical to fills. Confirm at the entry, not only at the close — read
  the working-order list **and** positions right after the order and classify it: `filled` (broker holds
  it, or the working order reports the full quantity), `partial` (`0 < filled < requested` → journal the
  broker's quantity and price, never the requested ones), `working` (order alive, nothing filled),
  `unconfirmed` (neither visible). Journal the row anyway so a real fill keeps the plan's own
  stop/take-profit levels, but carry `working`/`unconfirmed` as a **pending entry** that is re-verified
  on every cycle and closed at the entry price (zero P&L) as soon as the evidence says it never filled —
  that releases the concurrent-position slot in-cycle instead of at the day close. Keep the
  close-at-cost verdict behind a settlement grace (~2 minutes): an order that just filled can be gone
  from the working-order list while the position is not visible yet, and closing that row throws away a
  real holding's own exit levels. Report the verdict honestly to the owner — "entered and filled" and
  "order placed, fill unconfirmed" are different claims, and the submit response supports neither.
  Where the broker offers real-time execution events, they replace this polling.
- Order identifiers: an order-number field taken from the submit response is often 0/wrong. Key order
  identity off the broker's open-order list, not off that field.
- Duplicate submits on retry: idempotency is not guaranteed. Check quantity at the broker rather than
  inferring from a "already processed" style message. Retry only the failure class the broker labels
  as "request never went out" (bounded, with backoff); never blanket-retry a real remote rejection.
- Settlement lag: broker position data can lag a fill by seconds to about a minute -- right after an
  entry a code can read a partial quantity, or be missing from positions entirely. Reconcile at least
  a minute after the last fill, and never on every loop cycle: inside that window the broker
  legitimately looks flat and the "broker flat, local open" rule will close a position that was
  actually filled.  On a paper account the lag can run longer than a minute (measured ~2.5 minutes for a
  limit buy priced at the current quote, where the reconciler had already offered "journal-only -> close at
  cost"): re-read the open-order list before acting on that verdict, and never apply it while the order is
  still live at the broker — if it fills afterwards the position is held with no plan row carrying its
  stop/take-profit, so nothing monitors it.  **An unfilled-entry verdict has a shelf life**: the working
  order can fill minutes later, and then there is nothing left to repair — a mismatch measured a while
  ago is a snapshot, not a fact, so re-read the open-order list and positions in the same pass that acts
  on them.  Weigh the two repairs by reversibility: cancelling a live entry order is irreversible and
  can destroy an entry that was about to fill, while leaving the row costs a concurrent-position slot
  only until the day-close pass runs — check whether slots are actually scarce before cancelling
  The reconciler's default mode is already a preview (only
  `--apply` writes; there is no `--dry-run` flag, and passing one is rejected as an unknown argument).

  ## 5. Costs are missing from the journal (gross vs net)

  The same "figures look fine, the bookkeeping lies" class shows up in the cost dimension: a journal that
  records `(exit - entry) * qty` and leaves its fee column at 0 reports **gross** P&L, while the broker's
  own realised-P&L query (executed basis, per code) is **net** of commission and sell tax. The gap is
  invisible in the journal and it is the whole difference between a strategy that prints money and one
  that does not.

  - **Never assume fees are zero, and never invent a rate.** Derive the cost from the two numbers you can
    actually cite: `fee = journal_gross - broker_net` for the same code and day. Sign check: cost reduces
    profit, so the result must be positive when the broker is net of costs (journal -6,400 vs broker
    -6,516 → cost +116, ≈0.17 % of turnover).
  - **That subtraction only works for a name bought *and* sold in the same session.** The executed-basis
    query nets today's buys against today's sells, so a position carried over from an earlier session
    arrives with a per-code realised figure of 0: the subtraction then returns the whole gross as "cost",
    every row is flagged (negative cost / implausible ratio), and nothing has actually been measured. That
    is not a cue to supply a rate — for carried-over rows report the gross, state that net is unresolved,
    and leave the fee column untouched. Signature to confirm the cause before blaming the tool: the same
    query returns a populated average sell price for the code while its realised-P&L field reads 0.
  - **Check the unit of the broker's realised figure before deriving anything from it.** Some brokers
    report a per-code realised P&L rounded to a coarse unit (thousands of KRW), so a 2,233 KRW gross
    arrives as `2`. Then `fee = gross - net` returns nearly the whole gross on every row, so the pass
    flags *all* rows (`check(과대)` / 음수 비용) — that signature means a precision floor, not a code bug.
    Read the raw figures: small integers an order of magnitude below your gross ⇒ the source is
    truncated, the derivation is meaningless, so report gross, say net is unresolved, and **do not
    apply** (an applied pass writes nonsense costs that every later expectancy read inherits).
  - **Bound the day's costs with the broker's own equity delta.** Compare `end equity - start equity`
    against your summed gross: if they agree within rounding, measured costs sit inside the noise of
    your prices. The sign of the residual matters — actual *better* than gross points at entry/exit
    prices that differ from the fills (limit vs fill price, per-share rounding), not at zero costs; say
    which explanation the fills export supports instead of concluding "fees are 0".
  - **Write it by recomputing P&L from prices** (`pnl = (exit - entry) * qty - fee`), not by subtracting
    the fee from the stored P&L — otherwise a second pass double-charges and the row drifts. Idempotence is
    the test: run the backfill twice and the numbers must not move.
  - **Flag, do not silently write, the implausible rows**: a negative cost (broker better than your gross)
    or a cost above ~1 % of turnover means mismatched codes/dates, a wrong side, or a fee you have not
    understood. Report them for a human instead of "fixing" them.
  - **Match per code per day, allocate inside it**: the broker reports one realised-P&L row per code per
    session, so when the journal closed the same code several times that day, split the day's total cost
    across those rows by gross weight and keep the residual on the last row (the parts must sum to the
    total).
  - **Report the cost as the decision input, both ways, before live trading.** A tiny paper edge measured
    with `fees = 0` can be negative once costs are subtracted (an average holding of a few tens of
    thousands of KRW against a per-trade edge of a few hundred KRW is exactly that case), so state gross
    *and* expected net and let the owner judge; never present a gross per-trade figure as the strategy's
    expectation. The same reasoning applies to every "the paper account made money" claim.
  - **Order of operations at the close**: reconcile prices first (above), then attach costs — a cost
    derived against a wrong exit price is wrong twice.
