# Switching the broker account (paper → real): a state migration, not a re-login

Re-pointing a live loop at a real account changes the *book it acts on* while all the local state
(journal rows, counters, caps, schedules) still describes the paper one. Done as "log in and let it
run", the first minutes of the session can place real orders derived from paper-shaped state. Treat
it as a migration with an order of operations.

## 1. Freeze the machine before the human re-logs in

Do this **first**, because the human login is the trigger and it takes seconds:

1. Stop the loop process and confirm the pid is gone (task list / `tasklist /FO CSV | grep -i python`).
2. **Disable the auto-start paths, not just the process** — otherwise a watchdog restarts the loop
   within a cycle or two, or a weekday pre-open task starts it in the morning:
   `schtasks /Change /TN <loop-sup> /DISABLE` and `schtasks /Change /TN <loop-daily> /DISABLE`.
   Prove it: `/Query /TN <name> /FO LIST` must read 사용 안 함 / Disabled, and the process must stay gone
   across one watchdog interval.
3. **Leave the bridge/service supervisor enabled.** After the re-login the running service holds the
   stale session, and that supervisor's own staleness rule (N consecutive `connected:false` → restart)
   is what re-attaches it without a UAC round trip.

Why: the loop's entry gates read *broker* positions. A freshly switched real account holds nothing, so
every concurrent-position and invested-ratio gate reads "free" and a candidate list published earlier
in the day is immediately actionable — with real money, in whatever minutes are left of the session.

## 2. Retire the old journal before the new account writes to it

Stale open rows are not cosmetic: each one consumes a concurrent-position slot, the day-close reconcile
repeats "journal holds / broker does not" for every row, and a monitor that sells what the journal says
is held tries to sell names the account does not own.

Procedure (run it with the loop stopped, and via an interpreter on the side that owns the file):

1. **Checkpoint the WAL first** — `PRAGMA wal_checkpoint(TRUNCATE)`. A copy or move taken while a
   multi-MB `-wal` is pending silently loses those rows; the archive then looks complete but is short.
2. Move the database **with its `-wal`/`-shm` siblings** to an archive name that says what it is
   (`<account-kind>_<date>.sqlite3`), and keep it: it is the only record of the closed trades.
3. **Create the replacement with the project's own journal class**, not hand-written DDL — the schema
   version and any migration step live in that code (`Journal(path)`), and a hand-rolled empty file can
   be missing columns the loop later writes.
4. Write a one-page session summary next to the archive (closed count, realised P&L, the open rows with
   their entry prices, and the reason for retiring). Keep the account number out of anything committed:
   add `journal/backup_*/` and the archived `*.sqlite3` patterns to `.gitignore` in the same change.
5. Verify by reading both: archive counts equal the pre-move counts, replacement counts 0/0.

Gotchas that cost a run each:
- Journal schemas rarely have `entry_ts`/`entry_price`. A typical shape is one row per entry with
  `ts`/`price`, a `side`, and the exit filled into the *same* row (`exit_ts`, `exit_price`, `pnl`) —
  read the DDL before writing the query, then decide "open" as `exit_ts IS NULL`.
- A Windows-side python tool that prints Korean or an em dash dies with `UnicodeEncodeError: 'cp949'`
  *after* it has done its work, so a successful migration can exit non-zero with a traceback. Reconfigure
  the stream (`sys.stdout.reconfigure(encoding='utf-8', errors='replace')`) or keep such tools ASCII, and
  read the exit code together with the printed steps before calling the run failed.

## 3. Re-point the caps at the new account's risk budget

A paper account's caps are usually generous because cash was the real ceiling; the same numbers on a
real account are a licence to fill the book (a 100/day entry cap with a 20 % per-name cap across 5
names is ~100 % exposure). Decide the new numbers with the owner, then:

- Change them in **code defaults** and grep for a config file / env override first — if the launcher
  passes `--config <file>` or the project reads `TRADER_*` env vars, the code default is not what runs.
- **Update the test that locks the old defaults in the same commit.** These caps are usually asserted in
  a config-defaults test; leaving it green-by-not-running is worse than failing. Run the suite
  (`pytest <pkg>/tests -q`) and report the pass count.
- Confirm the *effective* values by instantiating the config classes, not by reading the file you edited
  (`python -c "from pkg.config import Config; print(Config().max_open_positions, ...)"`) — and remember
  a running process keeps the config it started with, so a restart is what makes it live.
- Keep the daily-loss auto-liquidation percentage where it was (or lower it) — that rule is the hard
  floor of the day and should not be loosened by the same edit that lowers everything else.

## 4. Emergency stop: say which lever does what

Two different mechanisms are easy to conflate and the difference matters when money is at stake:

- A **kill-switch file** is a *trading gate*: the next cycle sees it and stops trading; positions stay
  open and the file is not self-clearing. Ship a one-click pair (create + stop the loop / delete +
  restart) and say plainly in the script's own header and the runbook that **it does not liquidate**.
- **Auto-liquidation** belongs to the daily-loss rule (e.g. `-3 %` of the day's starting equity): it
  sells everything at market and halts. To flatten on demand right now, the owner sells in the broker
  terminal — a loop-driven exit path needs the loop running.

## 5. First-day verification (after the human logs in)

1. Health probe through the side that owns the service, expecting `connected:true`.
2. **Read the account number from the broker and assert it changed** — a switch that silently kept the
   paper account looks identical from every other angle (positions, cash, caps). Also note the starting
   capital: percentage caps scale with it, so the first report should convert them into KRW.
3. Re-read positions and the local book; with a fresh journal expect "no discrepancy, 0 held".
4. Only then re-enable the loop's auto-start (`schtasks /Change /TN <loop-sup> /ENABLE`) and let it come
   up before the next open — starting it mid-session is the case the freeze in §1 was meant to prevent.
5. On the first trading day, check the log for the caps actually binding (entry count, exposure %),
   rather than assuming the new defaults took effect.

Record the switch in the project's runbook as a section (what was migrated, the caps table, the emergency
levers, the revert steps) so it is not re-derived from the code next time.
