# Rehearsing a hard-stop guard (kill-switch file / halt flag)

Goal: prove the guard actually blocks trading, with no chance of the guard being consumed by a live
loop, ignored because the run never reached it, or left behind to fire at the next open.

## Preconditions

- The trading loop is stopped. Verify by process list (only the broker bridge should be up), not by
  assuming you stopped it.
- The broker is flat of working orders (`orders` endpoint empty) so the rehearsal cannot race a real
  one.

## Steps

1. **Write a throwaway config** beside the app and run everything through it:

   ```json
   {
     "market_open": "00:00", "market_close": "23:59",
     "start_before_open_min": 0, "end_after_close_min": 0,
     "state_path": "<state>.rehearsal.json",
     "journal_path": "<journal-dir>/rehearsal.sqlite3",
     "dry_run": true
   }
   ```

   The window override is what makes the loop reach the gate out of hours. Leave the guard path at its
   production default so the file you create is the file under test.

2. **RUN A - guard present.** Create the guard file (first line = the reason you want reported), run a
   single cycle with dry-run and the throwaway config. PASS means all three: the report's phase is the
   halt phase, the reason is that first line, and orders/exits are 0.

3. **RUN B - control.** Delete the guard file and repeat the identical command. It must NOT be halted
   (it should reach the check/decide/monitor phase). Without this you cannot distinguish "blocked by the
   guard" from "blocked by a cold feed, a closed session, or a bad config".

4. **Safety proof inside the same pass.** An order signal that is generated and then suppressed on the
   spot (an exit line followed by the executor's dry-run suppression message) is the evidence that
   dry-run gates orders. Also grep the loop for direct broker order calls: every order path must go
   through the one guarded executor, or dry-run is only as safe as the least disciplined caller.

5. **Clean up in one guarded pass.** Delete the guard file inside a shell trap so the failure path
   cannot leave it behind (`trap cleanup EXIT INT TERM`), remove the throwaway config/state/journal,
   then re-check: guard file absent, throwaway paths gone, production journal row counts unchanged.

6. **Repair the side effects.** A run on tracked runtime files (state json, shared log) modifies them:
   restore the tracked file from git and append a rehearsal note to the log so those lines are not
   later read as live activity.

## Pitfalls

- Running the rehearsal on default paths overwrites production state: the day's entry counters reset
  to 0 while the journal still holds the fills. Harmless at day end, actively misleading mid-session.
- A post-close single cycle reports its day-close phase and never evaluates the gate, so it passes
  vacuously. The reported phase is how you tell a real rehearsal from a vacuous one - read it, do not
  just check the exit code.
- Deleting the guard file is not the whole cleanup: confirm the file's absence *and* that no rehearsal
  state/journal was left being read as production. A guard file that survives to the next open
  liquidates the book.
- Rehearse the guard while the loop is stopped, never while it is live - a live loop reads the guard
  file on its next cycle and liquidates.
