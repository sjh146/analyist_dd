## The invariants

1. **The broker is authoritative for what you own.** Before trusting P&L, tuning parameters, or
   sizing a new order, reconcile local rows against `positions` + `orders`. An accepted order is not
   a fill: brokers return success for queued/unfilled orders, so a locally-recorded "position" may
   not exist. One broker notice can also cover **both** outcomes — the same "already processed" text
   arrives for an order that filled completely and for one that is still working unfilled — so the
   response classifies nothing: read the working-order list (`filled` vs `cancelable` quantity)
   together with `positions`, and while they disagree treat the row as unconfirmed. **A phantom row is
   a mid-session incident, not a close-time cleanup:** the moment it is written it consumes a
   concurrent-position slot and the per-code "already entered today" guard, so once the book sits at
   its cap every later candidate is refused for the rest of the session. Compare open rows against
   broker positions on every status pass, and report a row the broker does not hold together with the
   working order that explains it and the slot it is costing. Recipe, plus the rules for automating the
   pass at the day close (which classes the broker's own data may repair, and the one that must stay
   reported-and-open): `references/broker-vs-journal-reconciliation.md`, which points at the runnable
   `scripts/status_snapshot.py`.
2. **Never persist a derived price of 0 (or None) as a fill price.** Market orders usually come back
   with price 0 because the fill price is unknown at submit time. Fall back to the limit price, then
   to a live reference quote; if neither exists, refuse to place the order. Persisting that 0 books
   the *entire* position as a loss and poisons every downstream number (realised P&L, drawdown gate,
   Kelly priors).
3. **Fail closed on unknown inputs that feed risk; fail open only on tuning.** A missing quote means
   HOLD, never "price 0 ⇒ stop-loss hit". A missing cash figure means no buys, never "assume enough".
   Parameter files that are absent/malformed may quietly fall back to defaults.
   When the broker **refuses a derived figure** and your layer substitutes a more conservative one
   (measured: the orderable-cash query rejected for a paper account, with the broker's deposit field
   used instead), that is a legitimate fallback rather than an order error — but say which figure the
   gate used and mark the orderable amount as degraded, so a later sizing question is not answered
   from the substitute as if it were real headroom.
4. **Every rule cap must apply within a batch, not just once per cycle — and simulation must never
   consume the live budget.** A daily entry cap checked once before the order loop is defeated by any
   cycle that emits several candidates at once. The **book-level** caps (concurrent positions, total
   cost basis) carry the same flaw and hide it better, because every candidate in the batch is judged
   against one snapshot taken before the batch: each one sees the last free slot and all of them fill.
   The diagnostic is the broker's own position count exceeding the configured cap (12 codes held
   against a cap of 10, from a cycle that planned 3 buys while holding 9). Accumulate what the batch
   has already planned -- new codes and their cost basis -- and feed that into the gate; a cap that
   only reads the pre-batch snapshot is not a cap. Count a **new slot only for codes not already
   held** (topping up an existing name must not consume a second concurrent-position slot) while
   **every** planned order's cost basis goes into the invested total. Track simulated fills in their
   own counter, or a morning dry-run blocks live trading for the rest of the day.
   **The mirror image costs entries instead of safety: a slot spent on a candidate you then discard.**
   Candidates that the rule layer above the gate is going to throw away (stale per-name signal, entry
   window closed, price moved past the drift band) are still counted by the book-level gate, so they
   consume the slot and the next-best candidate is refused for that whole cycle — measured: the three
   leading candidates all failed the price-drift band while the fourth (inside the band) was refused as
   "already +N planned this cycle", so the day produced zero orders with a buyable name sitting in the
   published list. Filter those candidates out **before** the batch reaches the gate: compute the
   list-level regime/threshold rules first, on the *unfiltered* list, then hand the gate only enterable
   candidates so what the gate counts is what it
   can actually buy. Read those quotes in **small chunks** and **fail open**: a single batched read over the whole list can exceed the client's own request timeout and come back empty (measured: a 20-code batch against a 5 s client timeout), and when a missing quote is scored as "unquotable ⇒ reject" the filter then drops **every** candidate — a fail-closed trap that costs the whole day, worse than the slot waste it was fixing. Read a handful of codes per request, and if the read itself fails, keep the unfiltered list for that cycle. Keep the drift check inside the executor as well — that one is the fail-closed net,
 not the allocator.
 **A daily cap counted from the journal alone undercounts the moment a bridge call times out.**
 A request that answers `timed out [connection_failed]` can still have been accepted and filled by
 the broker, and that fill leaves **no journal row** — so the day's entry counter reads one short and
 the next cycle spends the phantom headroom on an extra live order (measured: 3 configured entries,
 4 real orders sent, the 4th resting unfilled). Derive the counter from the broker — codes with
 `buy_qty > 0` in the day's fills plus working buy orders plus locally pending codes — and take
 `max(journal, broker)`: this is a **real-order path**, so the fail-open side is the larger count,
 while an unreadable broker read falls back to the journal count (fail-safe, not fail-open to zero).
 Keep the two sources visible in the report ("journal 2 / broker 3") so a divergence is a finding,
 not a rounding. **Check the day-close pass before hand-repairing the row**: a reconciler that adopts
 broker-only positions usually writes it itself — with the timeout recorded in the entry reason — by the
 time you look after the close, and the right outcome is then your backfill refusing on the duplicate
 (`exit 3`), not a second row for one fill. Wait for that pass (or read its reconcile summary) before
 writing anything by hand. When it really is missing, repair from the broker's own executed basis with a
 backfill tool that defaults to dry-run and refuses when a same-day row for that code already
 exists — never invent the price from a live quote, and write the sell row too when the position is
 already closed, or the realised P&L never reaches the journal.
5. **The broker session can drop mid-run.** When connectivity/session state goes false: stop the loop
   first (so no further rows are written), hand the re-login back to the user (never guess
   credentials), reconcile, then restart. While the link is down, never present live P&L as fact —
   give the last measured value with the time it was measured and say what is blocked. A cycle whose
   health check fails skips the decision and order stages entirely, so a disconnected loop writes no
   rows on its own: do not kill it for that reason, and treat its own consecutive-failure halt as the
   stopping signal — clear the halt (resume) once the link is back, and start the process too when the
   halt ended it (a run loop that returns on the halt leaves nothing running, so `resume` alone changes
   nothing; `resume` then start is the ordered pair).

6. **Cap the book, not just the bet.** A per-order sizing rule (Kelly, fixed fraction) says nothing
   about total exposure: with a generous daily-entry cap the account fills with one correlated
   cluster and every stop can hit the same way. Add book-level caps -- concurrent positions and
   total cost basis as a fraction of equity -- and evaluate them before sizing. Watch the arithmetic
   when picking a prior: at 1:1 payoff `f* = p - (1-p)/b` is <= 0 for any `p <= 0.50`, so a "safe"
   pessimistic win-rate prior silently blocks every entry instead of halving size.  Before reporting such
   a rule as *applied*, establish which prior is live: a journal-derived prior arrives as keys in a params
   file (`prior_win_rate`, `prior_avg_win_pct`, `prior_avg_loss_pct`) and with those absent the gate runs
   on config **assumptions** — "Kelly sizes every order" and "Kelly sizes from measured results" are
   different claims, and only the first is true by default.  There is usually a **second** prior source upstream
   of the params file — the producer's own feed carries per-strategy stats (e.g.
   `scoring_summary.<screener_stats>.<strategy>` with `win_rate` accepted as either a ratio or a percent, plus
   `avg_win_pct` / `avg_loss_pct`), and the resolution order is **feed stats > own journal > config defaults**.
   Establish which one is live from the **published payload**, not from the config: a stats file written but
   never picked up (wrong directory, not re-published) leaves the gate on defaults while the setup reads as
   "measured".  A producer record carrying `win_rate` **without** the two average-P&L fields is unusable as a
   prior — the payoff ratio `b = avg_win/avg_loss` cannot be recovered from it, so the producer must be re-run
   with the extra fields rather than the reader inventing `b=1`.  A produced prior should be **shrunk toward
   break-even** before it is sent (e.g. `p' = 0.5 + (p-0.5)/2`, `b' = 1 + (b-1)/2`, clamped) because an unshrunk
   measured edge over-sizes; after shrinking recompute `f*` and if it lands `<= 0`, **publish nothing for that
   strategy** — a produced prior that silently blocks every entry is worse than no prior at all.  Aggregate a
   prior only from records produced by the **same model version** (a re-trained model's older rows are another
   model's results); if the record carries no model identity, keep only the latest producer batch.  The clamp is at zero (`f* = max(0, ...)`), so
   a below-break-even prior raises no error: entries stop through a zero-size gate whose wording reads
   like an empty market.  Break-even win rate by payoff ratio b is 50% at b=1, 40% at b=1.5, 33% at b=2 —
   so a real edge under break-even is repaired by raising the payoff, not by re-running the tuner.
   **Declare a score's unit and compare it in that unit.** The same silent-gate failure applies to entry
   thresholds, not just priors: a producer whose score is a probability × 100 (values 40-70) compared
   against a threshold meant for a 0-100 screener score blocks the whole strategy with no error — the log
   line reads like a truth ("average below threshold") while the scale, not the market, closed the path
   (measured: a probability feed averaging 0.55 under a 75-point threshold produced a week of zero
   entries). Require the publisher to declare the scale in the payload (`score_kind`: raw score vs
   calibrated probability vs percentile) and choose the threshold from that declaration; when the payload
   carries both a probability and a scaled score, assert they agree (`score == prob × 100`) so the
   declaration cannot drift from the number it labels. A percentile form (rank within the producer's own
   scored universe) survives a model retrain or a distribution shift, where an absolute probability
   threshold silently changes meaning — and it needs the producer to persist the whole scored universe,
   not just its top-N list, so check that artifact exists before promising the threshold is meaningful.
   **The same failure runs the other way: a guard that never fires because it reads a key the producer does not
   send.** A ceiling ("the list is strong but late": average day change above N % ⇒ block the whole screener) is
   inert, with no error and no log line, when it reads `extras['pct_change']` while the feed publishes
   `day_change_pct` — every candidate contributes nothing, the average list is empty, and the guard returns
   "no block". Measured: the pct ceiling had never fired on the screener feed, and the profile it was built for
   (the day's close list averaging +24.2 % against a 15 % ceiling — the same shape as the entry that lost
   31,455 KRW) passed unchecked. So resolve every guard's input through one helper that accepts each producer's
   name for the key, and **prove the guard can fire**: a zero-count reason line is a bug, not an empty case.
   Then read the effective value from the running process's own log line, not from the config file you edited.
   Corollary: a state-file counter written *before* the guard runs under-reports exactly when the guard is the
   only blocker (measured 1 while two screeners were blocked) — count after every guard, or the report lies.  A
   A self-tuning review that writes shrunk priors from the journal is therefore a **live deployment**, not a
     diagnosis: never fire its write mode unattended, and do not assume you can read its verdict without
     applying it — such a tool often has **no dry-run at all**, and the params file it rewrites is re-read by
     the engine on the next fetch, so "just looking at the output" silently re-sizes every entry made after it
     (measured: per-entry budget 1.5% -> 0.62% of equity, mid-session, with one command run to inspect it).
     Redirect the tool's output path to a throwaway file when you want the numbers without the change, and if
     it has already written, report the before/after size to the owner instead of leaving a quiet change in
     place. Which way it moved is a two-variable question: `f*` depends on the prior win rate *and* the
     journal's payoff ratio `b = avg_win / avg_loss`, so a 0.43 prior — below the b=1 break-even of 0.50 — is
     still positive at a measured b=1.4. Compute `f*` from the pair before telling the owner trading will stop:
     below break-even shrinks size, and only `f* <= 0` blocks entries.
   take-profit / trail / time-exit figures are derived, the knobs that move them, why a config change
   leaves open positions untouched, how the same lines also set the quantity, and the session-based
   shelf-life ladder (`max_hold_sessions` / `flat_stop_*` / `time_stop_*`) that closes survivors on a
   session counter instead of a price — orthogonal to the stop/tp row values, armed only inside the
   profile's close window, and failing open when a quote is missing:
   `references/exit-levels-and-sizing.md`.  One pair, not two knobs: a trailing stop whose give-back is wider than its arm threshold puts the exit line *below the entry*
   (an exit rule that sells at a loss), so keep `arm >= trail` and replay every proposed exit-width change
   against the recorded peaks (`hwm_price`) before deploying it.
