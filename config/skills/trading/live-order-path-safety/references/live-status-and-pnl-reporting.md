# Live status + total-return reporting (trader-agent, driven from WSL)

For "is it running right now?" and "what is my return from the starting capital?". Both answers
must come from the broker/OS, never from a file the loop writes.

## 1. Is it running? (three independent checks)

| check | command | healthy reading |
|---|---|---|
| processes | `/mnt/c/Windows/System32/tasklist.exe` (grep `python`, plus the terminal's own exe, e.g. `CpStart.exe`) | bridge pid + loop pid both present, terminal present |
| link | `/mnt/c/Windows/System32/curl.exe -s --max-time 8 http://127.0.0.1:8100/health` | `{"ok": true, "connected": true}` |
| liveness | `wc -c runner.log`, wait one cycle interval (≈300 s), `wc -c runner.log` again | byte count grew and the tail shows a new `cycle done (...); sleeping` |

The trader process runs on **Windows**, so `ps aux` inside WSL shows nothing — absence there is not
evidence of a stopped loop. The bridge binds **loopback on the Windows side**, so a WSL-side request
to `127.0.0.1:8100` fails and the Windows host IP (`ip route show default`) fails too; run `curl.exe`
from the Windows side as above rather than concluding the bridge is down.

A single log snapshot proves nothing: the loop sleeps between cycles, so the tail looks frozen for
minutes at a time. Observe **two** cycles before saying "running".

## 2. Reading the journal from WSL

The journal is a live sqlite file on drvfs with an active WAL. Opening it in place gives
`sqlite3.OperationalError: disk I/O error`, and copying only the main file gives a **stale, silently
inconsistent** snapshot (row counts and cost totals change between copies with no error). Copy the
trio, then query the copy:

```bash
rm -rf /tmp/tj && mkdir -p /tmp/tj
cp journal/trade_journal.sqlite3 journal/trade_journal.sqlite3-wal journal/trade_journal.sqlite3-shm /tmp/tj/
```

Sanity-check the copy against the live view before quoting a number from it (e.g. compare the open
rows against `/positions`); a snapshot that disagrees is stale, not news.

## 3. The account picture

```bash
/mnt/c/Users/jhshi/Python312-64/python.exe tools/portfolio_check.py --json   # run with cwd = the workspace
```

It reads positions/equity from the bridge and stops/entry from the journal, and flags
`journal_only_codes` / `broker_only_codes` (a position sold or never filled shows there instead of
being averaged into a return). Its `cash` is the broker's **orderable** cash, not settled deposit.

Capture that JSON through a Windows-side redirect (`cmd.exe /c "chcp 65001 >nul & cd /d <workspace> &
C:\Users\jhshi\Python312-64\python.exe tools\portfolio_check.py --json > tmp_port.json"`) and decode the
file with a fallback (`utf-8` -> `cp949` -> `utf-16`): the redirect still lands cp949 bytes for the
Korean names even under `chcp 65001`, and the raw file then reads back as "binary". Delete the temp
file afterwards — it is not a repo artifact.

Lighter path for the plain-text modes (`portfolio_check.py`, `daily_pnl.py`, `reconcile_journal.py`): pipe
them through `iconv` instead of redirecting to a file —

```bash
C:/Users/jhshi/Python312-64/python.exe tools/portfolio_check.py 2>&1 | iconv -f cp949 -t utf-8
```

That decodes the Korean holdings table, the exit-reason text and the reconcile verdicts in place; keep the
redirect + `utf-8`/`cp949`/`utf-16` probe only for `--json` output or when `iconv` aborts on a stray byte
(`iconv -c` drops it, `//TRANSLIT` transliterates). Note which interpreter each tool wants:
`portfolio_check.py` / `reconcile_journal.py` / `review.py` are 64-bit, `daily_pnl.py` is 32-bit.

Pitfall in the tool: it builds the state path by string-replacing a slash-separated substring inside
the journal path, so on Windows paths the replacement misses and `start_equity` comes back `null` —
and a null simply omits the day-P&L line instead of erroring. Read `loop_state.json` for
`daily.start_equity` directly (and take the first cycle's own `CHECK: day start equity` log line as
the cross-check) rather than trusting a null to mean "no data".

Run the journal-vs-broker comparison **after** the cycle that wrote the newest rows, not against a figure
you already have: `scripts/status_snapshot.py` (Windows 64-bit interpreter, stdlib only, `--repo` /
`--bridge` overridable) prints the broker's equity/cash/position count, the day's closed rows with the
realised total, every open journal row against the broker position that should back it, the working
orders with their `filled`/`cancelable` quantities, and each holding's distance to its own exit line. A
row the broker does not hold is the fault class that silently costs a concurrent slot all session, and it
is invisible in a `portfolio_check.py` run taken before that row was written.

## 4. Total return from starting capital

```
total_return_pct = (equity / starting_capital - 1) * 100        # equity = broker cash + securities evaluation
day_pnl          = equity - loop_state.json:daily.start_equity  # day baseline, a different thing from the start capital
```

The starting capital is the account's funding figure recorded in `bridge/api_notes.md` / `OPERATIONS.md`
for the paper account — use it, do not reconstruct it from the day-start equity.

Cross-check, and expect a residual rather than equality:

* journal realised P&L (`sum(pnl)` over closed rows) + unrealised (current evaluation − open entry cost)
  vs. broker equity;
* journal internal consistency: `(exit_price - entry_price) * qty == pnl` for every closed row;
* cash identity: `starting_capital - open_cost_basis - realised = cash`, computed with **one source per
  term**. Take `cash` and the holdings' cost basis from the broker's own `/balance` fields
  (`total_buy_amount`), never a journal cost basis (`sum(qty * entry_price)` — that is the plan price,
  not the fill). Stirred into a broker cash figure it manufactures a large fake gap: measured, a
  "-6,100원 저널/계좌 불일치" that was pure basis-mixing and had to be retracted. Same-source arithmetic
  closes to a few hundred won.

`/balance` field map (paper account, verified): `cash` == `d2_deposit` == `withdrawable`,
`total_buy_amount` = holdings cost basis, `equity` = `cash` + `total_eval_amount`; `broker_total_eval`
repeats the securities leg and is **not** equity; `buyable_error "94007 ... 매매불가 종목"` is the paper
server refusing a buyable-amount query and does not invalidate the rest of the payload.

The residual after same-source arithmetic is small and nameable — fees/tax absent from the journal
(`fees` column 0) plus rounding. Headline the broker's own equity rather than rebuilding the securities
leg from quotes: the two price bases differ by a few thousand won and the broker's is the account.
Quote the residual and its causes; never quietly tweak a component so the two agree — and when a gap
you already published turns out to be your own basis mix, correct it explicitly in the next message
with the reworked arithmetic.

Expect the **orphan decision** too: a cycle's decisions can read "already holding 10 codes (max_open
positions 10)" while the journal shows fewer open rows — the broker is holding the codes the same
cycle is exiting, because decisions are evaluated before the exits execute. It is ordering inside the
cycle, not a bookkeeping bug.

## 5. Report shape (Korean, for this owner)

1. one line: running / not, with the process pids and the measurement timestamp;
2. the headline: `시작자본 → 현재 equity = 손익 / 수익률 %`, stating the as-of time; keep the day's
   *equity move* (`equity - daily.start_equity`) and the day's *realised* P&L on separate lines with
   different labels — calling the equity move `당일 손익` conflates a pre-open-mark baseline with an
   entry-price baseline, and the two will not add up (that gap is not an error, it is two baselines);
3. the breakdown: realised (n closed) + unrealised (n open) and the residual, each labelled by source;
4. the "why" the owner is really asking for — win rate and payoff (wins, losses, average win vs average
   loss) so a losing total is explained by the rule, not by one bad name;
5. what is still open / blocked, then numbered options with a recommendation;
6. when the entry count is low, the **distribution** of block reasons over the whole day rather than one
   sample of it — a `last_cycle.plans: 0` with an empty `reasons: []` in the state file is *not* evidence
   of an empty or quiet candidate set: the loop records only its own skip reasons, while the engine's gates
   (a full book, a size that rounds to zero shares) land in the decisions rows, which is why the counts
   come from there — group the text column (`SELECT substr(reason,1,40) grp, count(*) n FROM decisions
   WHERE ts LIKE '<day>%' GROUP BY grp ORDER BY n DESC`). Counts separate the cases that look identical
   in a spot-check: a day dominated by `balance_position_amount` is a **full book** (not a quiet market),
   while `size rounds to zero shares` is a candidate that cleared every gate and could not be sized at
   all (Kelly/risk budget ÷ stop width below one share) — a sizing defect a cap check never surfaces.

Any number that came from a stale or derived source is announced as such in the same sentence.
