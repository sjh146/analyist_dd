# Offline harness layers (verify the order path without spending money)

How to test an automated order path before it runs live: isolate the four boundaries the strategy
reaches through — **broker, feed, clock, state/bookkeeping** — substitute a deterministic fake for each,
and leave the decision code untouched. A harness test is only worth having if it asserts a **number**
(qty, stop level, fill price, exit reason, state field), never that the run completed.

The layers below are the concrete instance in `C:\Users\jhshi\trader-agent`; the same four layers are
what any "can we trust this order path?" question needs.

## Layer 1 — in-process broker (`trader_core/mockbridge.py`)

A fake that exposes the *same duck-typed surface* as the real client (`health / balance / positions /
open_orders / quotes / order / cancel / subscribe / unsubscribe / shutdown`) and keeps an internal cash +
average-price ledger, so the engine's own cash / position / invested caps are exercised for real.
Documented fill rules of this one: limit fills at the limit price; market fills at a seeded last price;
market with no reference price is rejected (`no_reference_price`); buy over cash rejected
(`insufficient_funds`); sell over the holding rejected (`insufficient_position`); everything fills
instantly and in full.

Gotchas worth knowing before extending it: a `reset()` may restore a hardcoded starting cash rather
than the constructor value (build a fresh instance when the starting cash matters), and to run it
deterministically you also pin the config (market-hours gate off, a kill-switch path that does not
exist) and use an in-memory journal, or an after-close fixture evaluates to zero decisions.

**Extending it is where the value is**: the failure modes that have actually cost money on this path —
partial fill, order accepted but never filled, a fill price of 0/None, a price that disagrees with the
broker's own mark, a session that drops mid-cycle — are *not* in the default mock. Add them as seeded
modes (fill fraction, reject-after-N, zero price, mark divergence, disconnect on the Nth request)
before claiming the order path is covered.

## Layer 2 — network boundary (scripted HTTP server)

For the defects the in-process mock cannot express: unknown endpoint / 404, broker error envelopes,
degraded payloads (missing cash, zero or missing prices), transport failures. A real threaded HTTP
server on a random port serving a registry of `(method, path) -> (status, payload)` plus contract-shape
builders (`*_ok`, error envelope) reproduces the wire without the broker. Point the real client at its
`base_url` — this is the layer that proves the client's error handling, not the strategy's.

## Layer 3 — feed, clock, state injection

A real-shape feed payload served by a static feed client, **frozen clock constants** passed into the
loop's single-cycle entry point, and a state store pointed at a temp path. The loop's constructor
taking `bridge, feed, config, journal, state, notifier, engine, log` is what makes a whole half-session
deterministic; a run that touches the production state file is a rehearsal that will corrupt the live
day's counters.

## Layer 4 — one scenario test per guard

Each runtime rule gets its own test that names the gate and asserts its effect: entry threshold, entry
cap, missing quote skips the plan, daily-loss liquidate + halt + guard file, news gate (blocked /
fail-open / fail-closed), guard file present blocks the cycle without acting, auto-halt without
liquidation, resume clears the halt, day rollover resets counters, close cancels orders, first open
cycle produces a plan in dry-run. A guard whose suppression you have never observed is untested.

Run the suites on the deployment runtimes (32-bit bridge / 64-bit core+runner when the broker client is
COM-bound) and quote the pair when reporting a change.

## What no harness here reproduces — rehearse it live

Instant, full-quantity fills mean **partial fills, unfilled orders, a zero/None fill price, slippage,
broker session drops and clock skew cannot be simulated**. Those are precisely the defects that have
cost real money, and they surface on the paper account — caught by reconcile, the runtime guards and
the pre-open pass, not by a unit pass. Never report a green suite as evidence that the order path is
truthful.

## Missing piece: session replay

There is no harness that replays a **recorded day on a time axis** — feed fixtures are single
snapshots, so "would this parameter change have helped?" cannot be answered without waiting for more
market days. To build it: record the per-cycle feed snapshot plus a per-code quote path, drive the
loop's single-cycle entry across the session against the mock broker seeded from those quotes, then
diff the simulated fills/exits against the recorded outcome. It shares its data foundation with the
forward-return labels any learned model needs (candidate features + forward returns for *every*
evaluated candidate, not just the fills).
