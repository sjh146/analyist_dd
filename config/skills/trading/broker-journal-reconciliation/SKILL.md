---
name: broker-journal-reconciliation
description: Use when journal and broker disagree on an order.
---

# Broker vs journal reconciliation (live accounts)

The journal is the system's memory; **the broker is the authority**. Every reconciliation decision is
made from broker state, and a disagreement is never silently resolved in the journal's favour.

## Classify the order before acting on it

Run every questionable order through `trader_core/reconcile_rules.py::order_state()` (single source of
truth; tests in `trader_core/tests/test_reconcile_rules.py`) and act only on the verdict:

| state | evidence | permitted action |
|---|---|---|
| `filled` | broker `/positions` holds it, or `CpConclusion flag=1` | correct from the broker's own qty/price; a partial fill takes the **broker's** qty |
| `resting` | `cancelable_qty > 0` (still working) | **nothing** — never rewrite, never "close at cost" |
| `rejected` | `cancelable_qty == 0` and no broker position | close at cost (PnL 0) so the concurrent-position slot frees |
| `unknown` | broker unreachable / ambiguous echo | report and stop; escalate to the human |

**`cancelable_qty == 0` is the only thing that licenses a zero-PnL close.** "Not filled yet" and "will
never fill" are indistinguishable in the order echo — that confusion produces a phantom exit (the row
is written off, then the broker fills the order minutes later). Treat every other state as *report
only*: a wrong exit price is far more expensive than a stale row.

### A timeout is not a rejection — it can be a fill the journal never saw

`bridge request failed: timed out [connection_failed]` on an entry means the broker never returned the echo,
**not** that no order exists. Measured: a buy answered with a timeout at 09:05, the broker **did** fill it, and
no journal row was written — the loop then carried it as a holding from broker reconcile alone. Two silent
consequences:

- **The daily entry cap under-counts.** The cap is derived from journal/pending rows, so a filled-but-
  unjournaled entry does not consume it and the day spends one order more than the owner's limit (measured:
  `daily.entries` 3 of 3 while four orders had actually gone to the broker).
- **The position has no entry row**, so no stop/tp is recorded on it; only broker-side monitoring keeps it
  alive, and a restart is the moment it can be orphaned.

So after any timeouted order call, before anything else read `/positions` and `/daily_pnl` for that code. A
non-zero `buy_qty` with no journal row is a **confirmed fill** — adopt it immediately through the reconcile
path. When reporting the day's trade count, count **broker fills + working orders**, never "journal rows",
and state the discrepancy explicitly rather than quoting the counter.

**Check whether reconcile already did it before hand-writing the row.** The loop's post-close reconcile adopts a
broker-only position *and* closes it in one pass, writing `order_type='reconciled'` with an `entry_reason` that
records the timeout verbatim (measured: a −2,008원 position appeared as a closed row with the fill price, the
exit price and that sentence). A hand-rolled backfill run after that must refuse — build the tool with a
same-day-row guard that exits non-zero instead of inserting a duplicate (measured: `이미 매수 행이 있다` → exit 3).
Order: read the journal row → run/await reconcile → only then, if still missing, backfill.

**Implementing broker-truth counting** (the cap fix):

```
entries_today = max(journal_count, len(codes with buy_qty > 0 today + working buy orders + pending_entries))
```

- Add the broker read as a **new client method guarded by `getattr(bridge, "daily_pnl", None)`** — old mock
  bridges and test doubles lack it, and an unguarded call turns every existing loop test into an exception
  (or worse, a silent 0 that re-opens the leak). Fall back to the journal count when the method is absent or
  the read fails: the guard exists to make the cap **larger or equal**, never smaller.
- Fix the contract with tests for: broker count larger than journal / working order with zero fill / journal
  larger / bridge failure falls back / sell-only rows excluded. Run the whole `trader_core/tests` **and**
  `runner/tests` suites — a new required bridge method is a contract change for every fixture.

## Order of operations

1. Wait **>= 60 s after the last fill** before reconciling — positions settle over seconds to about a
   minute (one code read 3 shares, then 37 shares, 90 s later). Reconciling inside that window sees a
   flat broker and closes a live position.
2. Reconcile against the **pre-cancel** order snapshot — cancelling destroys the evidence that
   distinguishes a phantom entry from a position sold outside the system.
3. Broker-only position → adopt. Journal-only with an unfilled order → close at cost. Journal-only
   with **no** unfilled order → **report, never close**: the real exit was a broker fill and the live
   quote is not a fill price. The human supplies the broker's mean sell price
   (`tools/reconcile_journal.py --apply --fix-exit CODE=PRICE`).
4. Inverse case — the broker still holds a position the journal closed → the row **reopens** (the
   discarded exit is kept in `algo_json.reopened_exit`). Re-run: convergence can take two passes.
5. Never invent a price. Broker fill price, or an explicitly passed value — otherwise report and stop.

## Tooling isn't optional

```bat
Python312-64 tools\portfolio_check.py          :: P&L + journal/broker mismatch flags
Python311-32 tools\daily_pnl.py                :: CpTd6032 -- REAL 평균매도단가 / realised P&L
Python312-64 tools\reconcile_journal.py [--apply --fix-exit CODE=PRICE]
Python312-64 tools\reopen_phantom_exit.py --code <code> --yes
Python312-64 tools\sell_partial.py --code <code> --qty <n>   :: one-shot owner-approved exit
```

Run them on the **Windows interpreter**, never from WSL: drvfs reads of the live SQLite journal raise
`disk I/O error`, or silently miss rows still in the `-wal` file (fresh exits look invisible). If you
must inspect from WSL, copy `.sqlite3`, `-wal` **and** `-shm` into a temp dir and open the copy —
a WAL-locked live file raises `disk I/O error`, and a reader that swallows that error as "0 rows"
reports a false zero-trade day (seen 2026-11-20: a status script printed "청산 0건 / 실현 0원" while the
journal held 20 closed trades). Fail loudly instead, and never overwrite a saved state file from a
failed read. Any scripted read-modify-write of these
files needs `newline=""`, or LF silently becomes CRLF and the diff is the whole file.

Reading the live row for a status check (Windows interpreter, read-only):
`sqlite3.connect('file:journal/trade_journal.sqlite3?mode=ro', uri=True)`.
**Do not add `immutable=1`** — it tells SQLite to ignore the `-wal`, so trades booked minutes ago are
invisible and the journal looks stale while it is not (a same-day exit and re-entry read as "not in the
journal" this way). `immutable=1` is for cold copies only.

## Broker truth without the HTS: `GET /daily_pnl`

`tools/daily_pnl.py` cannot answer while the bridge holds the CybosPlus session (a second 32-bit client
answers `CybosPlus is not connected`). The bridge now exposes the same `CpTd6032` read-only as
`GET http://127.0.0.1:8100/daily_pnl` -> `{summary: {eval_pnl, realized_pnl, return_rate},
items: [{code, buy_qty, sell_qty, avg_buy_price, avg_sell_price, realized_pnl, return_rate}]}` (values are
Cybos strings). Use it as the evening check: `avg_sell_price` is the broker's **원 단위** mean sell price
and therefore the only legitimate price source for a journal correction.

- **`realized_pnl` is not in 원 — it reads as 천원, truncated** (-3,112원 -> `-3`, -266원 -> `0`).
  `trader_core/fees.py::derive_fees` assumes 원, so feeding these gives `fee_total = gross - net` of about
  -3,109 and a `check(음수비용)` flag. Correct prices from `avg_sell_price` only; leave fees at whatever was
  already booked until the unit is confirmed on a large-P&L day.
- **A bid-derived exit price is systematically wrong.** Exits booked from the order-time `bid` (or from a
  cash delta) differed from the broker's `avg_sell_price` by 22-51원/share and moved the day's realised
  P&L from -5,255 to -6,065. Re-check every market-order exit against `/daily_pnl` the same evening, then
  re-run the reports derived from the journal (feedback/screener scoreboards) — a corrected price makes
  every previously quoted figure stale.
- Correct through the journal API, not SQL: `Journal(path).correct_exit(trade_id, exit_price,
  fees=None, exit_reason="broker CpTd6032 avg_sell_price ...")` recomputes
  `pnl = (exit - entry) * qty - fee`. **The constructor takes the SQLite file path**
  (`<trader-agent>/journal/trade_journal.sqlite3`); passing the directory raises
  `unable to open database file`.

### Why the gap exists: market exits journal the trigger, not the fill

An exit signal that goes out as a **market order** (price=None) fills at the bid at execution time, while
the journal records the trigger price it was decided on. Measured one session: four exits off by
0.35–0.82% and one by 3.0%, all in the loss direction — the journal overstated realised P&L by ~1,800원
on a 244k account. A late-entry case ran the other way (broker 3,288 vs journal 3,305), so the check must
be two-sided, not "journal is always optimistic".

Consequences worth stating to the owner when quoting a day's result:
- Report **broker** `/daily_pnl` as the day's number; the journal is a second opinion that gets corrected.
- Attribution shifts: the same trades re-priced turned -200원 into -1,594원 for one code, i.e. the loss
  was in the fill, not the entry.

### Fix it as a batch, every day (don't hand-correct one row at a time)

`tools/sync_exit_prices.py` (Windows interpreter) reads `GET /daily_pnl` and rewrites today's closed rows
to broker `avg_buy_price` / `avg_sell_price` when they differ by more than ~0.3%, recomputing pnl. It is
idempotent — a second run reports `수정 0` — so schedule it (15:45, after the close, before any report is
derived) rather than remembering to do it. Rules that made it safe:
- Only **closed** rows of the **current** day; never touch open rows or another date.
- 0.3% threshold (below that is rounding) and skip rows whose broker qty is 0 or missing.
- Print a before/after table; the diff is the evidence that the day's headline number changed.
- **Schedule it from the WSL side as a thin wrapper that calls the Windows interpreter**, because the tool
  must run on Windows but the scheduler lives in the distro: a shell script containing
  `/mnt/c/Windows/System32/cmd.exe /c "C:\...\python.exe C:\...\tools\sync_exit_prices.py"` plus a
  timestamped log line, registered in the user crontab (`45 15 * * 1-5 <script>`). Register with
  `crontab -` (no root needed), then run **the script itself** — not the python line — once to prove the
  interop path resolves outside an interactive shell; a second run reporting `수정 0` is the idempotency
  check. Tool output that must be read on the Windows side still goes through the Windows interpreter:
  copying the live sqlite to WSL stays the read-only path, never the write path.

Pair it with an audit kind so a mismatch surfaces on its own instead of waiting for a human to notice:
compare journal `exit_price` vs broker `avg_sell_price` per code, flag > 0.3%, and treat it like any other
violation (`journal_fill_mismatch`). A run that goes `OK` after the sync is the proof the two stores agree.

### Measure before switching market→limit on exits

Market exits are correct for stops (guaranteed out) but cost 0.35–0.8% on tp/trailing. Do **not** flip
them to limit to "fix slippage": a resting sell leaves the position open with `sellable_qty` 0, i.e. an
exit that cannot be re-issued. First record the exit-time quote (`bid1`, `offer1`, spread) alongside the
exit row for a session; only with those numbers can you show what a limit would have filled at. Until
then the honest report is "applied and measured, change pending evidence".

## Manual (out-of-band) full exit

The loop closes a position only on stop / take-profit, so "sell this now" has no in-system path.

1. **Preview**: `Python312-64 tools/sell_partial.py --code <code> --qty <sellable_qty>` (no `--yes`).
   Its aborts are answers, not obstacles: disconnected bridge, error payload (half-dead session),
   `sellable < qty`, or a working order already live on the code.
2. **Execute** with `--yes`. Despite the name, `--qty` = the broker's `sellable_qty` is a full exit.
   The echo (`order_no 0`, `10725 매도주문이 접수되었습니다`) is not a fill; the verdict comes from the
   tool's own `/positions` + `/orders` settle loop, and `filled` means the broker position is gone.
3. **The sale is not booked yet.** The tool rewrites the open row's qty to what the broker still holds
   (0 on a full exit) and leaves `exit_ts` NULL, so the row still occupies a concurrent-position
   slot. Close that row yourself, from the broker's own number:
   - proceeds = `/balance` `cash` after minus before; `exit_price` = proceeds / qty (fee- and
     tax-inclusive, so PnL is slightly conservative and no price is invented);
   - back up `journal/trade_journal.sqlite3` **and** its `-wal`, then update `qty`, `exit_ts`,
     `exit_price`, `exit_reason`, `pnl` on that one row with `Python312-64`.
   `tools/daily_pnl.py` cannot supply 평균매도단가 while the bridge holds the CybosPlus session (a
   second 32-bit client answers `CybosPlus is not connected`) — which is why the cash delta is the
   price source here.
4. **Never blanket-run `reconcile_journal.py --apply --exit-price N` to do step 3.** It closes *every*
   broker-flat row, and a live buy order still resting at the broker (`cancelable_qty > 0`,
   `filled_qty 0`) also reads "broker is flat" to it — that is a `resting` row, which is untouchable.
5. **Expect the loop to spend the freed cash in its next cycle**, sometimes the same minute. Read
   `/orders` right after the exit and put the new order to the owner as a numbered decision, judged
   against the limits they actually set in `trader_core/config.py` — only call it a breach when the
   gate itself skipped it (`G skip: ... would breach per-stock N% equity cap` in `runner.log`).
6. **Restore `qty` when you close the row.** `sell_partial.py` rewrites the open row to what the broker
   still holds (0 on a full exit), so the closed row would record a 0-share trade; set `qty` back to
   the sold quantity and `pnl = (exit_price - entry_price) * qty`.

**`report_only` is a snapshot, not a to-do list — re-read the row before asking the owner for a
price.** A buy that never filled stays in the journal as an entry row; the close cycle reports
`CLOSE flat but journal open: <code> ... the quote is NOT a fill price, so nothing was written` and
lists it in `daily.reconcile.report_only`, yet the loop's own `_verify_pending_entries` closes it at
cost (PnL 0, `exit_reason='unfilled: ...'`) a cycle or two later, and
`loop_state.json -> pending_entries` is cleared at the date rollover (`runner/loop.py::_roll_day`).
So check `exit_ts` on the row (and whether the loop process is still alive) **before** escalating:
hand-fixing either the row or `pending_entries` invents work for the owner that the system already
finished.

## Realised P&L and fees expire with the trading day

`CpTd6032` answers for the **current session only**; asked about a past day it returns `0건`, which
reads as "no fees, no realised P&L" instead of "no longer available". Book P&L and fees **on the
settlement day** (an evening tick right after the close). A missed day is a permanent gap — say so;
never schedule or promise a backfill.

## A capital hole is not a strategy loss (measured 2026-11-09)

When equity falls but the journal's realised P&L does not, the missing money was never in the system:
manual trading outside the loop, or a withdrawal. Measured: equity 524,861 (2026-10-09) -> 296,123
(2026-11-09), journal realised only -6,837 over the same span; the owner confirmed ~223k of manual
losses. Nothing in the journal, `trader_fills`, or `CpTd6032` (current session only) records them, so
the gap stays unexplained for as long as nobody asks.

- Diagnose from `/balance` alone, no journal history needed: `equity` vs `total_buy_amount` vs
  `total_eval_amount`. `equity ~= stock_eval` with `cash 0` means the account is fully invested and the
  loop cannot enter anything new — a cash problem, not a signal problem.
- **`broker_total_eval` is not `equity`.** The payload carries both; `broker_total_eval` is the broker's
  own aggregate and can be roughly twice the account's equity. Reading it as equity reported a ~527k
  account for one that actually held ~247k, which silently mis-scaled every threshold derived from it
  (loss-limit %, position-weight %). Use `equity`, and sanity-check the field by comparing it with the
  loop's own `loop_state.json -> daily.start_equity` — same order of magnitude means you read the right key.
- Hand-traded leftovers enter the journal only via reconcile as `entry_reason='reconcile: adopted from
  broker'` with `order_type='reconciled'`, `screener='reconcile'` and `algo_json={}` — that signature is how a
  manual position is identified later. **Give adopted rows a screener label (`reconcile`), never an empty
  string**: per-path statistics filter on the `screener` column, so an empty value silently drops those trades
  from every path's scoreboard while still counting them in the day's P&L (the two reports then disagree and
  the cause is invisible). Identify manual positions by `order_type`/`entry_reason`, not by an empty column. It then obeys the default stop/tp (7 %), so its exit line exists even though no
  signal produced it.
- Say the number plainly and ask the owner whether it was a withdrawal or manual trading; never model
  it as strategy P&L, and never 'explain' it by picking a quote price.
- Publish it upward: a manual loss equal to 43 % of capital while the loop was idle for a month is a
  risk-control finding, not a footnote. Report the equity series with dates whenever equity is quoted.

## Changing the code that does this (live order path)

These files gate real money, so an edit's blast radius is measured in blocked or duplicated orders:

- Put any new helper **inside the class that calls it** — anchoring an insertion on "the first `def`
  in the file" lands in an unrelated class, the method does not exist on the engine, every gate call
  fails closed and the entry path stops dead. Verify with `grep -n "def <name>" <file>` (one hit, in
  the right class) before running anything.
- Then run the **full** suite that covers the path (`trader_core/tests` **and** `runner/tests`), not the
  new test alone. If it comes back red, revert first (`git checkout -- <file>`), reason afterwards, and
  report the failed attempt — a half-wired live path is worse than an unwired one.
- **Contracts that encode the old rule must be updated with it.** Grep the test tree for the behavior
  you are changing (e.g. a fixture that expresses a "working order" via a non-zero cancelable qty)
  before declaring green — an end-to-end test asserting the previous semantics will fail, and its
  failure is the contract change, not a bug.
- **Separate the count cap from the risk cap.** Per-path slot limits (e.g. "3 swing + 2 daytrading")
  are a *count* concept: raise the concurrent-code cap while leaving the invested-% cap untouched — the
  KRW budget is where the risk is denominated, so more, smaller positions is not a bigger bet. The
  journal's own `screener` filter (`journal.open_trades(screener=<name>)`) is the only place the path is
  recorded; broker position objects do not carry it.
- A feed/path that real orders already consume must stay **bit-identical** for those consumers when you
  add a new path beside it; verify by diff, not by the new test passing.
