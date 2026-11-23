## Overnight carry and the next open

Positions held past the close are only as safe as the next session's start, and that start fails
silently. Verify each link instead of assuming the schedule is still good:

- **Prove the unattended start path on the machine that owns it**: query the scheduler entry and read
  back its next-run time and status, and check the interpreter path and working directory inside the
  task definition — a task that was never created, or created with quoting the other shell/parser ate,
  never runs and never complains.  A next-run time alone is **not** evidence the start happened: also read
  the task's **last result** and its **logon mode**, then compare the processes' own start lines against
  the scheduled time.  A registered task with a next-run time, a non-zero last result and an
  interactive-only logon mode means the run was started by hand that day — report the daily start as a
  human step until that result is clean, and never tell the owner it will run itself.
  **Decode that result code and read the task's own conditions before calling the start path good.**
  `certutil -error <code>` names it (a refusal reads 운영자 또는 관리자가 요청을 거부했습니다) and the
  task XML's `<Settings>` shows *why*: a wizard-created task carries `DisallowStartIfOnBatteries` /
  `StopIfGoingOnBatteries`, so a laptop on battery refuses its own start, while an interactive-only logon
  mode refuses it in a session that is not interactive.  A last-run timestamp that lands *late*
  (mid-morning for a pre-open schedule) is the scheduler's missed-run catch-up firing and being refused,
  not a session that ran — the refusal leaves no other trace, so the loop's own log is the only place the
  gap shows.  **Find the gap in the session banners, not in file mtimes:** the run entrypoint writes an
  `=== log opened pid=...` line at every start, so `grep -n "log opened" runner.log` read against the
  tail's last cycle date says whether a session existed for today's open and when it began (include any
  rotated `.1` logs in that grep).  No morning banner means every exit line went unwatched into the open:
  say so, bound it with the windowed breach check rather than the day's low, and offer the scheduler
  condition change as the fix to apply — never as something already in place.
- **A leftover guard/kill-switch file liquidates the whole book at the next open.** Rehearse the guard
  with the loop stopped, delete the file, and re-check its absence in the same pass; never leave it
  present overnight and never rehearse it while the loop is live.
- **A rehearsal that never reached the gate proves nothing.** Outside the session window the loop takes
  its day-close path and returns before evaluating the halt/guard gate, so the run looks successful
  with the guard untested. Require the cycle report to name the gate (`phase=halted` plus the guard
  file's own reason line), force an in-window schedule with a throwaway config when you must run out of
  hours, and add a control pass without the guard file so you can tell the guard from a cold feed.
  Recipe: `references/guard-gate-rehearsal.md`.
- **Any run on the default paths overwrites the production state file — isolate it.** Point the state
  and journal paths at rehearsal-only files, then confirm the production journal's row counts are
  unchanged, restore any tracked runtime file the run rewrote, and mark the shared log so those lines
  are not later read as live activity. A mock run stamping a stale session date is harmless (the date
  rolls over and counters reset), but a `closed` latch left on the *current* date turns the morning
  start into a silent no-op — check the state file's date against today's session date before trusting
  the latch or the entry counters.
- **Know the fallback candidate source and test it while the primary one is down.** A remote collector
  being offline does not have to end the day if the loop can rank/scan locally — confirm that path
  rather than reporting the day as dead.
- **Clear your own clients before asking for the login — and re-check the port guard afterwards.** A live
  client process attached to the vendor session holds the terminal's own data files, so the login's
  updater cannot replace them: the downloads actually complete while the patch step exits non-zero, and
  the failure looks like a network error (the owner reads it as "the login is broken"). Pre-login check,
  in this order: your client processes gone **and** its port not LISTENING, the required tunnel/network
  probe answering, the terminal not half-up (helper/service processes resident with no main window), and
  your own elevation decided (`whoami /groups | grep S-1-16-12288` — High means you may start it
  yourself, Medium means the owner's UAC console). Afterwards confirm the sign-on from the terminal's own
  launcher log, then start the client and prove it with an account query, not a connect flag. Two traps
  ride along: the same single-instance port guard that prevents two writers also **skips** the scheduled
  start while the stale process still owns the port (so a scheduled repair after a re-login is a no-op —
  kill the pid, then start), and on a Windows host the client is read from the distro with the Windows
  binary (`/mnt/c/Windows/System32/curl.exe -s --noproxy '*' ...`): the distro's own curl is refused and
  the inherited `HTTP_PROXY` otherwise answers 502 for a healthy local service.
- **The human step gates everything.** A broker login cannot be automated: it must happen before the
  scheduled start, and any local service that was already running must be restarted after the re-login —
  a process started before it holds a stale session and keeps reporting disconnected.
  **Do that restart yourself when the supervisor has already given up on it.** A bounded supervisor goes
  into a give-up state (one `RE-LOGIN REQUIRED` line, then polls at minutes); waiting for its next poll
  leaves the book blind for no reason once the human has logged in. Stop the service's own process and
  start it again, then prove it with the broker's own query (`/balance` ok, not just a connect flag) —
  the supervisor notices the terminal's host processes changed, resets its restart budget and logs the
  healthy line on its next poll, so no state file needs editing. Check whether you are allowed to do it
  before handing it over: an interop child inherits the elevation of the Windows terminal that hosts the
  distro, so `whoami /groups | grep S-1-16-12288` (or a WindowsPrincipal `IsInRole('Administrator')`)
  decides between doing it now and asking the owner for UAC.
  **The step has a window, not just a deadline.** An idle session can expire on its own, and an
  unattended stretch with no requests is exactly when it does — so a login performed the night before
  does not survive to the open. Schedule it minutes before the open and start the components in order
  (sign-on confirmed in the terminal's own launcher log → local service → loop); a login that worked and
  then dropped reads as "the restart worked, then it broke", which looks like a code bug and is not.
  Before blaming host power settings, read them: a sleep timeout of `0` (never) rules autosuspend out,
  leaving lid-close / manual suspend as the human-side causes.
  **Give the alert the deadline and the measured cost, not just the requirement** — "login required" does not
  convey urgency, while "login by the window's deadline; missing it cost X KRW last time" does. Take X from the
  measured audit (the exit-window slippage) rather than estimating it, and put the deadline in the message body
  next to the step, since the alert is often read long after the window it names.
- **A login window that never appears is a network symptom before it is a credentials one.** Before
  scheduling the human sign-in (or asking for a repair/reinstall), prove the terminal's own *updater* can
  reach its vendor servers. A line that only carries proxied HTTP(S) completes the TCP handshake and then
  resets the data phase, so the vendor's per-component download logs `connect success` + a download
  failure + a retry loop and the launcher sits at its file-check step forever — no login window, nothing
  for the owner to type. Read the updater's own log (it names the host, port and the failed fetch) and
  expect the vendor's anti-tamper module to refuse its repair tool outright, before handing over a human
  step. The fix belongs to the network path: `wsl-host-ops` → `references/proxy-only-network-line.md`.
  **A healthy tunnel is not proof the vendor's traffic is inside it.** Verify per port before closing
  the network hypothesis: `curl.exe -x http://<proxy>:8080 http://<vendor-ip>:<port>/ -m 20` and the
  same through the tunnel with `--noproxy '*'`. When both answer `000` (timeout) yet the launcher still
  handshakes and transfers bytes, the app binds the physical interface and the tunnel never sees it —
  its resets are the line's, and only retry-and-resume moves it forward. Prove the tunnel itself with
  one large transfer *through* it (`curl.exe --noproxy '*' -m 200 -o NUL -w
  '%{http_code} %{size_download} %{speed_download}' https://speed.cloudflare.com/__down?bytes=25000000`):
  a completed 25 MB file means the path is fine and the app's own connection is the problem.
  **A launcher stuck before login paints the login panel and ignores every click** — that is an
  unfinished file patch, not a credential failure, so do not ask for the password again. Use a tab
  switch as the control: when the panel's tabs do not even toggle, input is being swallowed. Poll the
  updater's temp file size (growing = still downloading; stalled plus a `ret==3 or 4 error` line in the
  updater log = it aborted), and remember that **each restart of the launcher resumes the patch from
  the partial file**, so repeated restarts converge where one attempt cannot.
  **Escalate the kill of a protected launcher in this order**: `taskkill /F /PID` (refused by the
  vendor's anti-tamper), `WM_CLOSE` (ignored), relaunching the exe (single-instance mutex — it only
  focuses the wedged window and does **not** restart the patch) → a SYSTEM scheduled task → the owner
  ending it in Task Manager → reboot. Ask before the SYSTEM step and say why. Do not read a synthetic
  `mouse_event` click as evidence that the UI is alive; the tab control above is the test.
- **One symptom, three causes — discriminate with a fresh probe, not with the process list and not
  with a retry.** Run a one-shot connect check in a *new* process and compare it against the running
  service's own health answer: fresh connects + service says false = the service holds a stale session,
  restart it; **fresh also fails = the broker link itself is down and no restart can fix it**, so hand
  the login to the human and restart the service afterwards (the running process keeps the dead
  session). Process presence does not discriminate: the terminal's helper/service processes stay
  resident after it exits, so a grep-based "the terminal is running" reads as a stale service when the
  truth is a missing login. The terminal's own launcher log is what settles the human step: it records
  the sign-on result (the CybosPlus sign-on dialog logs its own success line), so read it before telling
  the owner their login did not take — a recorded success plus a failing fresh probe means the link is
  down on the broker's side, not that the credentials were never entered. Never restart the service repeatedly or re-probe in a loop — each restart
  is a cold start and the answer cannot change until the human acts.
  When the link drops mid-run (a healthy cycle, then health false on the next one), the consequence to
  report from the last measurement is exposure, not P&L: **no exit can be placed while it is down**, so
  the open book is unprotected — give the last measured marks with their timestamp, each holding's
  distance to its own exit line from that measurement, and how long until the loop's own check-failure
  halt lands (failures x cycle interval).
- **Verify the running instance's mode from the process, not from a `status` subcommand.** Status
  commands typically re-read the *default* config, so they can print `dry_run=True` and the default
  candidate sources while the live process was started with live flags and a different feed. Read the
  process command line or the process's own start line in its log before reporting the mode to the
  owner or "fixing" a run that was never in the mode you read.
- **Whether an auto-halt leaves a live process is implementation-specific — verify, then act.** A loop
  that halts itself after N consecutive health-check failures may keep running and skip cycles, or its
  top-level run loop may *return* on the halt so the process is gone (measured: a 17-minute link outage
  ended the process, and the book stayed unmanaged until someone noticed). Read the process list and the
  log's last cycle before telling the owner it is "paused"; when the process is gone the recovery is
  `resume` **and** start, in that order — starting first re-reads the halt flag and the new process exits
  again, and a restart can also trip the day-close latch. The deadline is computable before the open: the
  auto-halt lands roughly N x cycle-interval after the scheduled start, so tell the owner how many
  minutes the human step has. If you own the loop, make a connectivity halt a *pause* instead of an exit:
  keep cycling, and clear it automatically only when **the same checks that failed** pass again (link +
  balance + feed freshness) — a healthy link alone must not clear a halt whose cause was a stale feed.
  Loss-limit and kill-switch halts stay hard stops that need a human.
- **Carry forward every position that missed an exit.** When a guard is added *because* a stop failed,
  the position that motivated it is still open, at a loss well past its stop, and it is the first thing
  the open must clear. Put it and every holding whose last mark came from the suspect side of a price
  divergence at the top of the next session's checks, and confirm the exit **fills at the broker** in
  the first cycle after the open — a started loop is not an exited position.  A gap during which nothing
  watched the book makes those catch-up exits price at the *current* mark rather than at the trigger, so
  they can land well past their own stop (measured: -13.9% exited against a -12% stop): report the fill,
  its distance past the trigger, and the downtime as the cause — that gap is the cost of the unmonitored
  window, not a stop malfunction. A sell still in flight
  makes that same code report "no sellable quantity" (the shares are committed to the pending order):
  that is in-flight, not a failed exit, so settle it from the position list a couple of cycles later
  instead of re-issuing the order.
- **An exit that is gated by a session window does not happen late — it happens a session late.**
  Resolve the exit mode and its window from the profile in code before promising or scheduling any exit:
  a "sell at the next open" position is only due inside that window, so a loop that was not up (or not
  logged in) during it leaves the position untouched with `time_exit_due -> False`, and the same code
  holds for another full session. The cost is not just the price drift — the position keeps occupying a
  concurrent slot, so the day's own entry window stays blocked too; report those two consequences
  separately. When the owner wants it closed anyway, that is a deliberate out-of-band exit: ask for the
  approval explicitly, and run the ordered recipe in `references/broker-vs-journal-reconciliation.md` §2b
  (sell → confirm flat at the broker → price the journal from the broker's own executed-basis average,
  never the live quote → reconcile with that price → export fills).
- **A partial reduction has no in-system path — and the loop will oversell after you make one.** Exits
  close the whole row (stop / take-profit / time), so "sell half at the open" is an out-of-band order
  like the one above, with its own trap: the journal still says the old quantity, the monitor sizes its
  exit from that row, and the next stop attempt asks the broker to sell shares that are already gone
  (a rejected order at best, a double exit at worst). Use the partial-exit tool rather than a hand-rolled
  `POST /order`, then repair the row in the same pass:
  1. `tools/sell_partial.py --code <code> --qty <n>` — preview only; it aborts when the bridge is
     disconnected, `/balance` does not answer, `sellable_qty < n`, or any working order exists for that
     code, and a single-use lock file stops a scheduled retry from selling twice. `--yes` submits the
     market order.
  2. Verdict from `/positions` + `/orders` (echo is not a fill). On `filled`/`partial` set the open
     journal row's `qty` to the broker's remaining quantity and record the order echo under
     `algo_json.partial_exit`; keep the entry price untouched.
     **`--qty` = the broker's `sellable_qty` is a full exit, which the tool does not finish:** the row is
     left at `qty 0` with `exit_ts` NULL, still counting against the concurrent-position gate. Close it
     yourself at the broker's own number — proceeds = the `/balance` `cash` after minus before, `exit_price`
     = proceeds / qty (fee- and tax-inclusive, so the journaled PnL is conservative and no price is
     invented) — after backing up the journal file **and** its `-wal`. `tools/daily_pnl.py` is not an
     option while the bridge is up: a second 32-bit client answers `CybosPlus is not connected`, which is
     exactly when a manual exit happens. Do not substitute
     `reconcile_journal.py --apply --exit-price N`: it closes every broker-flat row, and a buy order still
     resting at the broker also reads "broker is flat" to it. Full ordering:
     `broker-journal-reconciliation` → "Manual (out-of-band) full exit".
  3. Time it with a one-shot task instead of staying awake: `Register-ScheduledTask -Once -At
     <yyyy-MM-ddThh:mm:ss> -Principal (Interactive / RunLevel Highest)` plus a settings set built with
     `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`, then read back `NextRunTime`,
     `Principal.LogonType` and `DisallowStartIfOnBatteries` — the wizard default refuses to fire on a
     laptop on battery, and a task whose next-run time reads right can still be unrunnable.
  Report the reduction as "cash freed / concentration cut / stop line on the remainder", not as a P&L
  event: the day's realised number only becomes true once the broker figure is read.
  **The freed cash is spent by the loop's next cycle, often in the same minute** (measured: a market sell
  at 13:52:36 and a new 1-share limit buy at 13:52:39). Read `/orders` immediately after any out-of-band
  exit and put the resulting order to the owner as a numbered decision; judge its size against the caps
  the owner actually set in `trader_core/config.py`, and only call it a breach when the gate's own line
  said so (`G skip: ... would breach per-stock N% equity cap` in `runner.log`).
- **A window guard that reads current state cannot detect the window it already missed — audit the fills.**
  A live check conditioned on open positions ("if a position is still open past the window, alert") stays
  silent in exactly the failure it exists for: reconciliation zeroes the position minutes later, so when
  anyone looks there is nothing left to fire on. Measured: three exits filled at 09:26 against a 09:00-09:10
  exit window (the loop itself started 09:19), and the loss — -1,814 KRW, the day's entire realised loss —
  was visible only in the executed-fills record. Keep a **separate periodic audit over the fills export**:
  for every closed trade compare the exit timestamp against the profile's window *and* the exit price against
  the window's reference price (the session open), then report the count plus per-name slippage. Run it after
  the window closes, alert once via a state file rather than every cycle, and quote its measured cost in the
  human-step alert. Read the fails from the **broker's own fills export**, not the journal: a journal may
  record entries only (measured: 3 buys, zero sell rows), so a journal-only audit reports a clean window that
  never happened.
- **Attribute a breach to the gap with windowed data, never with the day's low.** A day's low below an
  exit line cannot say *when* it happened: the low may predate the entry, predate the moment a trailing
  line existed (`hwm` only moves while the loop is watching), or be the open of a session that started
  before the loop did. Split the day into windows (before the loop started / monitored / the gap /
  after) using 1-minute closes from any public chart source — no broker requests needed — and compute
  the line as it stood in each window; a breach landing in a pre-entry or pre-arm window is not a missed
  exit, and an intrabar spike inside a minute is invisible, so report the result as the bounded claim it
  is: "no exit line was touched in the windows we can see".
- **Recompute the day's headroom before the open.** Remaining concurrent slots and remaining total
  cost basis are separate ceilings that both gate entries; derive both from the positions actually
  held (not from yesterday's counters) so the first report of the day states how many new positions
  are even possible.

Ordered pass with the commands and the healthy/not-healthy reading for each step:
`references/preopen-readiness-pass.md`.
