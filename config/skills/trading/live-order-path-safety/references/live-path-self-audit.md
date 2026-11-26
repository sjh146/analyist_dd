# Live-path self-audit loop (read-only checks + a daily improvement queue)

A loop that watches a live order path for the defects the strategy itself cannot see, and hands a
prioritised queue back to the agent. Read-only during the session; code changes stay a separate,
verified step.

## Shape

1. **Invariant probe, every few minutes while the session is open** (a cron entry, not an agent turn).
   Cheap HTTP/file/sqlite reads only. Each probe writes one report (`reports/<x>_audit_latest.json`) and
   appends findings to `reports/<x>_findings.jsonl`.
2. **Findings carry a fingerprint** (`sha256(kind|detail)` truncated) and the writer skips fingerprints
   already on disk — otherwise a five-minute cadence turns one defect into a wall of duplicates and the
   daily queue becomes unreadable.
3. **Daily queue** (after the close): group the day's findings by kind with a sample line each, plus the
   current verdict, into `reports/<x>_improve_queue_<YYYYMMDD>.md`, and append a pointer row to a history
   jsonl. The queue is the hand-off to the next fix step; it is not itself a fix.
4. **Fix order:** evidence (reproduction numbers) → patch + tests → live observation of the changed
   behaviour. A finding is closed only when the probe that caught it returns clean.

## The checks that earn their keep

- **broker positions vs journal open rows**, in both directions: `broker_without_journal` (an unmanaged
  holding — its stop can never fire) and `journal_without_broker` (a ghost that will be sold into
  nothing). These two catch nearly every order-path incident before the owner does.
- **stop breach while still open**: compare the quote (`current`) against `entry × (1 − stop_pct)` from the
  journal row. A breach that the monitor has not acted on is a hard failure, not a warning.
- **working orders with `filled_qty == 0`** (and their age): the leftover of an unfilled limit order.
- **feed liveness and shape**: consumer port returns 200 with a byte count, `generated_at` inside the
  freshness bound, and the gated field actually present in each item (a missing field is a finding, and
  distinct from a value below the threshold).
- **liquidity of every published candidate** against the floor the screener claims to apply — this is the
  check that proves a filter change is real rather than configured.
- **journal exit price vs the broker's own average fill** (see the repair section below): a closed row
  whose recorded exit differs from the broker's per-code average sell by more than ~0.3 % is a bookkeeping
  defect that inflates or deflates every expectancy number derived from the journal.
- **halt state with open positions** (the loop's state file says `halt` while the broker reports holdings):
  a halted loop can short-circuit before its monitor phase, so positions go unmanaged while every other probe
  is clean. Treat the combination as a hard failure ("청산이 멈출 수 있음"), not a warning. Judge the halt from
  the state file **and** from a running cycle: after auto-resume the file can still read halted until the next
  cycle writes, and a halt that never clears looks identical to one that was never set unless you watch a cycle.
- **repeated exit failures for one code** (parse the runner log tail for `exit failed <code>`; ≥3 hits is a
  finding): one refusal is a transient quote read, several in a row is a position that cannot be closed and
  needs the operator now. This shows up in the log hours before it shows up in P&L — and the log lines age out
  of the tail, so the probe has to run on a short cadence to see it at all.
- **zero-quantity or non-positive-price entries** in today's rows.

## Repair, not only report: bring the bookkeeping back to the broker's fills

A closed row's `exit_price` is whatever the monitor saw when it fired, not what the broker filled. The gap is
systematic (market exits, slippage on both sides) and it corrupts every later expectancy calculation, so the
audit closes the loop with a repair step rather than a warning:

1. Read the broker's own per-code daily payload (`/daily_pnl`-style: `avg_buy_price`, `avg_sell_price`,
   `buy_qty`, `sell_qty`). Codes with `sell_qty == 0` have no fill to compare against — skip them.
2. For each of today's *closed* journal rows with a matching broker code, compare entry and exit against the
   broker averages; beyond a small tolerance (0.3 % is a sane default) rewrite `price`, `exit_price` and
   recompute `pnl` from the broker numbers.
3. Run it after the close on a schedule (one cron line), not ad hoc — every day that skips it leaves the
   journal drifting from the broker.
4. Re-run the probe: `journal_fill_mismatch` must return clean. The finding is closed only then.

Watch the units: per-code realised P&L payloads that arrive truncated to thousands make a naive comparison
look like a huge discrepancy. Compare **per-share average prices**, which carry the right magnitude, and let
quantity times the price delta produce the money figure.

## Optional: give the loop write authority (an agent cron, not a shell cron)

The read-only probe + evening queue leaves the fixes to whoever is awake. When the owner asks to
"keep testing and fixing during the session", the queue becomes an **agent cron** job (Hermes cron with a
prompt, the live-path skills attached, and `workdir` set to the analysis repo) that runs 2–3 times a day
inside the session. Same probes, plus a bounded repair step. What keeps it safe:

- **One defect per run.** Find the cause from logs/responses, fix that, run the full suite, commit with the
  measured evidence and the revert command, push. Report `applied but unverified` until a later cycle shows
  the new behaviour; never mark it verified from the unit suite alone.
- **Escalate, do not implement, anything that changes risk:** stop width, exposure caps, entries per day,
  market→limit on exits, promotion of a model. Those go back as a numbered option with the measured numbers.
- **Two independent records:** the job's own text output lands only in
  `~/.hermes/cron/output/<job_id>/<timestamp>.md` — a CLI session has no delivery channel, so say that in the
  report instead of promising a message.
- **Verify the wiring by running it once** (`cronjob action='run'`) and reading that output file: a scheduled
  job that silently errors on its first real slot wastes a session. Check that its probes actually ran
  (counts in the output), not just that the job status is `ok`.
- The job is only as good as the probes: if the audit is clean it should say so in three measured lines and
  stop — an agent cron with nothing to fix that "improves" something anyway is the failure mode.

## Guardrails

- The probe must be **read-only**: no journal writes, no order calls, no config edits. A self-audit that can
  change state is indistinguishable from the thing it is auditing. The repair step above is a **separate**
  tool run by the scheduler after the close — never inside the probe.
- **Reading a WAL-mode sqlite owned by another OS:** copy `<db>`, `<db>-wal`, `<db>-shm` into a temp dir and
  query the copy; opening the original yields `disk I/O error` and looks like corruption.
- **No code changes while the session is open.** During the session: probe, diagnose, queue. Apply and
  test after the close, then on the next session verify the probe that raised the finding now returns clean.
- Exit codes matter to the scheduler: `0` clean, `1` violations found, `2` could not judge (bridge/feed down).
  A probe that returns 0 because it could not read its inputs is worse than no probe.
