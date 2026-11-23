# Pre-open readiness pass

The pass to run before a session opens, or before restarting an unattended loop mid-day. Purpose:
by the time the market opens, every input the loop depends on is *proven* healthy, the day's counters
are fresh, and any human step has a deadline. Run it in this order — each step's output is the evidence
the next step assumes.

Report per `Reporting to the owner` in SKILL.md: the one blocking human step and its deadline first,
then the numbered steps with the measurement that proves each one.

## 1. Time

- Compare local time against an external timestamp (an HTTP `Date` header is enough). Healthy: skew
  within seconds. A skew of minutes/hours invalidates every window, focus-window and rollover rule at
  once, so nothing further in this pass means anything until it is fixed.
- Check the machine's own NTP state as the fix path, not as the measurement.

## 2. The broker terminal process

- List processes and look for the trading terminal's executable.
- Absent alongside `connected:false` => the human login has not happened. Say so and stop trying;
  retries and restarts cannot fix it.
- Present alongside `connected:false` => the local service holds a stale session; restart the service.

## 3. The local service

- Hit its health endpoint and read the connection boolean, not just `ok:true` — an `ok:true` response
  with `connected:false` is the normal shape of a healthy process with a dead broker link.
- Then pull the one account call that gives **total assets** (cash leg + securities leg). A broker
  "total evaluation" field frequently means the securities leg only; treat that as a position value,
  never as day-start equity.
- Read the service's own log tail to confirm which process id is answering — an old instance that
  never exited is a common reason a restart appears to do nothing.
- Prove any call that carries a query string separately (a quote-by-codes GET, a ranking page). A
  nested-quoted one-liner issued through the platform bridge (WSL → `cmd.exe /c curl "…?codes=a,b"`)
  has come back with an **empty body and exit 0**, which reads as "no quotes". Put the URL in a
  one-line `.bat` in the workspace and run that; and treat an empty body from a bridge GET as a failed
  call, not as an empty result — re-issue it before concluding anything about the instrument or the
  link.

## 4. The running loop's actual mode

- A `status` subcommand prints the *default* config, so it can show dry-run/default feeds for a live
  process. Confirm the mode from the process command line, or from the loop's own start line in its log
  (which also carries its feed source).
- Read that log, not a shell redirect target: a detached process launched with block-buffered stdout
  leaves the `.out.log` empty for hours while the in-process log file is current.

## 5. The unattended start path

- Query the scheduler entry for the loop (and the service) and read back next-run time **and status**.
- Confirm the loop's start time is still ahead of you. If it already fired, you are in recovery mode:
  see step 8.

## 6. The state file

- Session date equals today, and the day-close latch is cleared. A latch left on the *current* date
  turns the morning start into a silent no-op.
- Halt flag not set; consecutive-failure counter zero; the guard/kill-switch file absent (re-check the
  absence in this same pass — a leftover guard liquidates the book at the next open).
- Note that runtime state files are often both tracked and ignored: a rehearsal that rewrote one and
  a `git checkout` that restored it leave the file with a fresh mtime and stale content. Trust the
  content's date, not the mtime.

## 7. Broker vs journal

- Compare the broker's position count, codes and quantities against the journal's open rows, and list
  outstanding orders. Recipe: `references/broker-vs-journal-reconciliation.md`.
- Run this at least a minute after the last fill (positions settle over seconds to a minute), and never
  inside the window where a fill is still propagating — a flat broker there reads as a phantom close.

## 8. Headroom arithmetic (`max_open_positions`, cost-basis cap)

- Remaining concurrent slots = cap minus positions held right now.
- Remaining total cost basis = cap (fraction of equity) minus the current `buy_amount`.
- Both gate entries independently, so state the smaller of the two as today's real entry capacity. At a
  generous daily-entry cap the cash and book limits bind long before the entry count does.

## 9. Carry-forward watch list

- Every position already past its stop because an exit could not be trusted last session, and every
  position whose last mark came from the suspect side of a quote-vs-mark divergence.
- These are cleared by the first cycle after the open, so the verification is *at the broker* a minute
  or two after the open: quantity gone, and an exit row with a real price (never 0). If the exit did not
  fire, exit it manually rather than waiting for another cycle.
- Batch this with the day's first mark check: a stop that silently does not fire is the failure this list
  exists to catch.

## 10. Recovery if the loop already started while the link was down

- The auto-halt is state-only: the process is alive and skipping cycles. After the link is restored,
  clear the halt (resume) — do not restart the loop, which can instead hit the day-close latch.
- Deadline math for the owner: auto-halt lands about N x cycle-interval after the scheduled start, so
  the human step has that many minutes.
