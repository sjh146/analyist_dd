## Before going live

- Run the entire pipeline against the real (paper) account in dry-run and read the per-decision
  output: candidates → plans → gate reasons. Prove the suppression at the executor -- an order signal
  computed and then visibly suppressed on the spot -- and check that every order path funnels through
  that one guarded executor; a flag whose suppression you never observe is untested. A silent
  zero-count is a bug, not an empty market.
- The offline layers that let you test with no broker attached — in-process mock bridge, scripted HTTP
  bridge, feed/clock/state injection, one scenario test per guard — together with the failures **no**
  harness here can reproduce (partial fills, unfilled orders, a zero fill price, session drops, clock
  skew) and the missing session-replay piece: `references/offline-harness-layers.md`. A green unit suite
  is not evidence that the order path is truthful. A **red** suite is no evidence either until the
  environment is excluded: the project's live tuning/params files sit in the directory the tests read,
  so config/feed tests fail against the tuned values (candidate sources, thresholds) rather than
  against your change — stub or move that file, re-run, and only then read a failure as yours.
- Then place the first live order and immediately confirm it at the broker (`positions`/`orders`).
  A success payload from your own layer is not evidence the broker accepted it.
- Know the one-command stop before you start an unattended loop, and keep the two levers distinct when
you describe it: a **kill-switch file** that the next cycle reads is usually a *trading gate* (no new
orders, positions left alone, not self-clearing), while **liquidation** belongs to the daily-loss rule —
say which one the owner is pulling. Prefer the gate file over killing processes from memory, and settle
the project's own semantics from its code rather than assuming either behaviour.
- **Probe every external veto the order path depends on, and keep its local server alive.** A veto service
  that fails closed is a total blocker: with its endpoint retired or its server not running, every
  candidate is refused and the log line reads like a market judgement (`news gate unavailable … new
  entries blocked`) while nothing can be bought for the whole session. Verify three things before the
  session — (1) the configured URL answers at all (a default can point at a host that no longer exists),
  (2) the server's own query matches the **current** schema (a column that moved makes it answer 500,
  which the caller counts as "unavailable"), and (3) it can still return `block` for a name with known
  bad news: a gate that only ever says `unknown`/`ok` is untested, not verified. Keep it up with a
  short-interval guard (check the port, restart if absent). When a session ends with zero orders, walk
  the layers in order — regime threshold → per-candidate freshness → entry window → drift pre-filter →
  book slots → external veto — reading each layer's own reason line instead of guessing which one closed.
- **A fresh `generated_at` on an empty candidate list is a producer failure, not a quiet market.** The
  consumer's freshness gate checks the *publish* timestamp, so a producer whose inputs stopped being
  regenerated still ships a payload stamped seconds ago — with zero items — and the loop then reports
  `0 decisions`, no error, nothing to chase. Whenever the day closes with zero candidates, read the
  payload's per-strategy item counts and the producer's own job log: a line like
  `산출물이 N일 지났다 (> 3.0일) — 이 전략은 빈 리스트로 발행` names the real cause (inputs stale, not the
  market). Recovery is to re-run the producer's own wrapper once (`<scheduler wrapper> && <publish
  wrapper>`, as its cron would) and verify three things: items > 0 in the served payload, the consumer
  parses it (`scan --dry-run` showing candidates blocked only by `market_hours` outside the session),
  and the producer's job log carrying today's date. Do not "fix" it in the consumer by relaxing the
  freshness guard — that converts a loud gap into silently trading stale signal.
- **Changing the broker account (paper → real) is a state migration, not a re-login.** Freeze the loop
  *and* disable its auto-start/watchdog tasks before the human logs in (a loop left running has no
  positions to manage on a fresh real account, so every book-level gate reads "free" and the day's
  already-published candidates become real orders); retire the old journal (WAL checkpoint → archive →
  recreate with the project's own journal class) so stale open rows cannot eat concurrent-position slots
  or drive sells for names the account does not hold; re-point the caps in code defaults *with* the test
  that asserts them; and verify the **account number** changed after the login. Ordered procedure,
  gotchas (WAL loss, journal column shape, cp949 print death) and the emergency-lever split:
  `references/paper-to-live-account-switch.md`.
- **One book, one loop — make the start wrappers single-instance.** A hand-started loop plus a scheduled
  task firing later (or a second bridge on the same port) is two writers on one book: duplicate exits,
  duplicate entries, and every cycle asserting on state the other just changed. Guard the wrappers
  themselves instead of remembering to disable the schedule — refuse to start when the app's own process
  is already listed (loop) or the port is already listening (bridge), logging one line that names the pid
  before exiting 0. Keep the guard's process query in a `.ps1` file: an inline `powershell -Command
  "... | ..."` inside a batch `for /f` backquote block leaks `^|` to the interpreter, fails silently and
  starts the very second instance it was meant to prevent. Prove both directions (free = starts, occupied
  = refuses with a non-zero exit) and treat the guard as part of the deploy, so a manual start stays safe
  with the schedule left enabled.
- **Liveness must be watched, not assumed: the start is only the first check.** A component started in a
  console window dies with that window and records nothing — no exit line in its own log, an empty stderr
  file — and the loop's connectivity halt then *hides* the gap: `cycle skipped: halted (check failures 3:
  ... not connected)` means no exit is evaluated at all, so the book is unprotected for as long as the halt
  lasts, in or out of session. Measured: the bridge vanished before the open, the halt landed minutes into
  the session, and nothing was noticed until hours later. So (a) re-check PIDs and link health at a fixed
  cadence while the session is open, not only at startup, (b) tell the owner to **minimise** those console
  windows and never close them — or put the start under a hidden supervisor task that relaunches its child
  (recipe: `wsl-host-ops` -> `references/windows-scheduled-tasks.md` §5), and (c) when you report the book
  as managed, name the PID, the link probe and one *second* observed cycle.
- **A supervisor's health test needs one authoritative query, and its restarts must be bounded.** A
  client-side connect flag (`connected:true` from `IsConnect`, a socket that is still open) stays true over
  a session the broker has already dropped: the terminal window closes, its helper/service processes stay
  resident, and every request answers a transport-level error (`DibRq 통신요청 실패`-class) while health
  still reads green. So the watchdog must corroborate health with a **real account query** (a balance or
  position read must return ok), and treat "health true + query fails" as its own state — *half-dead* —
  which a restart cannot fix. Bound the recovery: restart **once**, then stop restarting, record
  `RE-LOGIN REQUIRED` and back off (minutes), because each further restart is a cold start against a session
  only the human can restore.  **Bound EVERY restart path, not just the one you already fixed.** A supervisor
  can carry a bounded recovery for one failure mode while a sibling branch restarts forever. Measured: the
  half-dead branch gave up after two tries and backed off to 5-minute polls, but the "not connected" branch
  restarted the bridge every 3 polls (~97 s) and reset its own counter on each restart — 37 restarts/hour for
  eight straight hours, 322 broker processes spawned in a day, and the log tail looked perfectly calm because
  each restart logged the same `bridge connected`-class line. Count it: a backlog probe on restarts-per-hour
  is the cheap detector, and the supervisor's own `started`/`stopped` lines grouped per hour confirm it.
  Reset the counter on a *healthy* connection, never on the restart, and give the second branch the same
  give-up-and-back-off shape. Do not make one expected GUI process (a window process name) the liveness
  test: a healthy session was measured running with only the terminal's component processes and no window
  at all, so requiring the window makes the supervisor refuse to act after every login. Test the **set** of
  the terminal's processes (any of them) plus the account query. Keep the log honest too: a state machine
  that alternates between a steady warning and "ok" every poll prints both lines forever — track the warning
  separately so it appears once until it changes.  
  **The bound belongs on every restart path, and the counter must not reset itself.** Measured on a
  disconnected (not half-dead) session: the stale-restart branch reset its own counter on each restart, so
  while the terminal's leftover components were up but no session existed it restarted the service every
  ~97 s — 37 restarts/hour, 322 processes in a day, endlessly (the half-dead branch in the same file was
  already bounded, which is why only that path's guard was tested).  Bound it the same way (N restarts ⇒ log
  `RE-LOGIN REQUIRED` once, then poll at minutes), and reset the budget only on **evidence** — a real account
  query answering, or the terminal's host process set changing (a human re-login) — never on the restart you
  just performed.  Give the supervisor a rehearsal mode that (a) logs instead of stopping/starting and writes
  its own log file, (b) can force the unhealthy state, (c) shortens the polls, and (d) can fake the
  "processes changed" signal from a file; then prove both halves: N attempts → one give-up line → zero
  further attempts, and a changed generation → budget reset → attempts resume.  
  **Before killing anything, identify the production instance by an attribute the rehearsal does not have**
  (here: `-WindowStyle Hidden` in the command line, parented by the scheduler) — a cleanup filter matched the
  live supervisor and killed it; the recovery was the redeploy that was coming anyway, verified from the new
  process's own start banner and the service's health query.  Also never `pkill -f <script name>` from a
  shell whose own command line contains that name: it terminates the shell itself mid-step.
- **A day-close latch makes an evening restart a no-op.** An unattended loop that already ran its
  end-of-day sequence keeps a `closed` flag and exits immediately when started again; its counters
  reset only when the date rolls over. Start it before the session opens (and only after the broker
  session is verified healthy), or the "restart" silently does nothing until the next day.
- Latch the day's starting equity from a *total-assets* figure. A broker field named something like
  "total evaluation" often means the securities leg only; using it as equity fabricates a large loss
  and can trip the daily-drawdown gate into liquidating everything.
- **Elevation belongs to the owner, and a launcher's silence is not a stop.** A process started from an
  elevated console cannot be killed from a non-elevated one (the kill answers access denied), and the
  same denial covers changing a scheduled task's settings — which is the fix for a misconfigured start.
  Requesting a UAC prompt programmatically buys a dialog the owner may never see and which resolves as
  "the user cancelled". Do not drive elevation yourself: hand over the exact one-line command (or the
  project's stop/start wrappers) for the owner's elevated console and say what it changes. Then verify
  the stop by **process id**, not by the wrapper's output: take the id from the entrypoint's own start
  banner (`grep "log opened" runner.log bridge.log`) because command lines are frequently unreadable
  across an OS boundary (null `CommandLine`), and a wrapper's internal kill step fails silently when its
  `for /f` / quoted `/FI` filter is mangled by being invoked through another shell's `cmd.exe /c` — the
  old process keeps running and the wrapper prints nothing. Silence is not a stop, and a process listing
  on the wrong side is not evidence either.
- **A broker re-login only sticks with a clean component set.** A session that died mid-run and was
  re-logged in by the owner can still answer disconnected: the running service holds the stale session
  (restart it — only after the check passes, never in a loop), and a **duplicate** helper/COM server —
  two instances of the terminal's own server process where there was one — keeps the new session from
  attaching, so the fix is a full exit of every terminal component, then one clean login. Do not use a
  visible-window check as the discriminator: a working terminal may have no titled window at all. Count
  instances against client connections rather than treating the number as the signal — a client's own
  connect can spawn a second server instance that disappears again when that client exits, so two
  instances while a client is connected is not by itself the duplicate that blocks a re-login.
- **Before acting on "that change is live", age the running process against the edit.** Compare the
  process's own start banner with the edited file's mtime: a process older than the file is running the
  previous code no matter what the file now says. Ask this *before* proposing a restart, because after
  the close the day-close latch makes the restart a no-op and the change lands at the next open instead.
- **A read-only engine scan does not exercise the rule layer that lives in the loop.** Rules split
  between the engine's gates and the loop's own (entry threshold, per-day and per-code budgets,
  price-drift skip, news gate), so a scan can report several actionable plans while the loop blocks
  every one of them. Verify each layer where it runs -- the scan for the engine, the loop's log and
  decision rows for the rest. And never run a one-shot loop (`run --once`) beside a live one: they own
  the same state file, so the one-shot can overwrite the live day's counters. Read-only scan plus the
  live log is the pair that tells the truth.
- **A running process keeps the configuration it started with.** Editing a code default, or a config
  file the process reads only at startup, changes nothing in the live process -- and the `status`
  subcommand will happily print the new value while the running loop still trades the old one. Restart
  the process, then prove the change landed from the new process id and the next cycle's own log line
  (the effective filter values it reports), not from re-reading the file you edited. Restarting **while
  the session is still open** is safe and is the normal way to deploy a loop-code change mid-day: the
  day-close latch only bites after the close, so the day's entry counters and starting equity survive —
  confirm that from the state file rather than from the restart having worked. When a watchdog/supervisor
task owns the loop, the reload is narrower: identify the loop process by its command line (the
broker-side service runs a different entrypoint and interpreter — never kill that one), kill only that
pid, and let the supervisor bring it back within its poll interval; the schedule does not need touching
for a mid-session code deploy.  **The supervisor's own start line is part of the configuration.**
A watchdog relaunch that omits the runtime flags silently reverts every config-file-driven change on
the next automatic restart — measured: a stop/start cycle came back with the code-default screener set
because the supervisor's `Start-Process` argument list carried no `--config`, so the day's trading
profile disappeared while every log line looked healthy. Fix both halves in one pass: add the flag to
the supervisor script **and** restart the supervisor process itself (a running PowerShell loop keeps the
script it loaded at launch, so editing the `.ps1` changes nothing until it is relaunched), then prove it
from the **new child's** command line that the flag is present — not from the file you edited.
