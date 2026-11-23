---
name: live-order-path-safety
description: Use when running live/paper orders against a real broker.
---

# live-order-path-safety

The invariants that keep an automated order path, its local bookkeeping and its risk gates
truthful when real money (or a paper account that is meant to behave like one) is involved.
Broker-agnostic: applies to any implementation of "strategy decides → order goes out → journal
records what happened → risk gates read the journal".

## 모의 → 실계좌 전환 (2026-09-28 실측)

전환은 "로그인만 바꾸면 되는 일"이 아니다. 순서를 지켜야 실주문 사고가 안 난다:

1. **루프와 그 감독을 먼저 끈다**: 루프 프로세스만 죽이면 `trader-loop-sup`(30초 워치독)이 즉시 되살린다.
   `schtasks /End` + `/Change /DISABLE` 을 **감독과 08:50 예약 작업 둘 다**에 걸어야 멈춘다.
2. **모의 저널을 은퇴시킨다**: 모의 미청산 포지션은 실계좌에 없다. 그대로 두면 동시보유 게이트가 유령을
   세어 신규 진입을 막고, 마감 대조가 '저널 보유/브로커 없음'을 반복하며, 없는 종목을 팔려는 시도가
   생긴다. `PRAGMA wal_checkpoint(TRUNCATE)` → 파일 이동 → `Journal()` 로 빈 스키마 생성
   (`tools/retire_paper_journal.py`). WAL 을 접지 않고 복사하면 그날 변경분이 사라진다.
3. **한도를 계좌 크기에 맞춰 다시 정한다**: 모의 기준 값(일 100건 · 종목당 20% · 동시 5종목)은 실계좌에서
   무제한에 가깝다. 값은 코드 기본값 + 그것을 고정하는 테스트까지 함께 고친다.
4. **사이징이 계좌 크기에서 성립하는지 계산한다**: `f* x kelly_fraction x equity` 가 1주 가격보다 작으면
   주문이 0주로 만들어져 조용히 아무것도 안 산다(53만원 계좌: 종목당 8,001원 → 10,000원 종목 0주).
   `risk.sized_qty()` 로 가격별 수량을 **실측**하고 필요하면 `kelly_fraction` 을 올린다(계좌가 커지면 되돌린다).
5. **사용자 보유 종목은 자동으로 보호되지 않는다**: 제외 목록 기능이 없으면 편입하지 않아도 R4(-3%
   일일손실) 전량청산에 포함된다. 편입/제외/매도 중 무엇을 할지 **사람에게 먼저 묻는다**.
6. 재로그인 직후 브리지는 낡은 세션을 들고 `connected:false` 로 남는다 → 감독이 3회 폴링 후
   재기동하거나 `bridge_run_admin.bat`. **계좌번호로 실계좌인지 확인**한 뒤에만 진행한다.

전환 직후 확인: 스코어보드가 빈 저널에서 크래시하지 않는지, 수수료 반영(백로그 T1)이 net 기대값을
바꾸는지 — 모의 +111원/건은 `fees=0` 인 **gross** 였다.

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

## Strategy-scoped limits (per-path slots) and the feed contract behind them

- **A gate is only as strong as the age bound on the artifact it reads.** A new entry gate (regime,
  volatility, exposure) that reads a **published artifact** is bypassed by any payload that is still
  inside the consumer's freshness window: with a 24 h feed-max-age, a payload published at 14:05 yesterday
  is still "fresh" at 09:05 today, so the gate never sees today's state and the first session after the
  change trades exactly as before — while the log says the gate is enabled. When adding a gate, set the
  artifact's max age **shorter than the gap between two publishes** (8 h against a 08:35 pre-open publish
  leaves 30 min of slack at 09:05) and verify the bound by asking what the previous day's payload would
  score. Change the test that pins the default in the same commit and say it was an intended default change.
- **A regime gate needs three explicit behaviours, and the third is the one that gets forgotten.**
  (1) Compute the state into a small JSON with an `asof` date, `state`, and a human `reason` string;
  (2) refresh it **inside the same wrapper, immediately before the producer runs** — a gate that only
  accepts today/previous-trading-day `asof` fails open the moment the refresh step is missing, which is
  silent; (3) on block, emit **zero candidates plus the reason in the payload/log**, never a quiet empty
  list — "후보 없음" and "regime_off: <index> close<ma20" must be distinguishable in the log, otherwise an
  empty day is indistinguishable from a broken producer. Fail-open is right for `unknown`/missing/stale
  state (keep trading on the old behaviour, warn once), fail-closed is right for a fresh `risk_off`.
  Prove both directions before shipping: forced `risk_off` ⇒ 0 items + reason line, and the gate disabled
  (or `risk_on`) ⇒ the normal candidate count, so an over-blocking gate cannot masquerade as a quiet market.
- **A gate that reads a local artifact is inert wherever that artifact is invisible — and fail-open makes
  it silent.** The same producer run from a bind-mounted script directory inside a container saw no
  `data/state/market_regime.json` (only `scripts/` was mounted), so the regime gate read `None`, passed,
  and produced 20 candidates on a day the host run correctly produced zero. Diagnose by diffing **both
  paths on the same day**: `md5sum` of the producer inside and outside (same code ⇒ the input is the
  suspect) plus `docker exec <c> test -f <gate-input>`. Fix the data mount, then re-run the producer's
  own dry-run and require the **block reason string** in its output — a visible file is not evidence the
  gate fired. Keep a per-run visibility check in the wrapper (loud WARN instead of a silent fail-open) and
  pin the policy with a test that fails if the call starts passing `--regime-block off` or the compose
  mount disappears, so a later autonomous tick cannot quietly re-open the path.
- **Verify the upstream actually publishes candidates for a path before opening capacity for it.** A consumer
  that already lists a strategy in its screener set still trades nothing if the feed never carries that key:
  measured, the published payload had two keys while the consumer's config already included the third path, and
  its own decision rows for that path were zero across every session. Widen the limit and the day still produces
  nothing — with no evidence to validate the wiring against. Order: (1) read the published payload's keys,
  (2) count the consumer's own decision/order rows **per strategy** (a per-strategy count of 0 in the consumer's
  journal is the measurement; the config file is not), (3) only then touch the caps. When the producer *has* a
  screener for that path but no publication step, publishing is the fix — adding slots is not.
- **A per-strategy cap cannot read broker positions.** Broker holdings carry no strategy attribution, so
  "3 of this path / 2 of that one" is countable only from the local journal rows (its strategy column, using the
  journal's own filtered query when it has one — a hand-rolled group-by over all open rows is unnecessary).
  Keep the global cap and the per-strategy caps as **two separate checks**, let a strategy without a configured
  slot fall back to the global cap (so an untouched path keeps exactly its old admission range), and **fail
  closed** when the journal cannot be read — if you cannot count the slots you cannot promise the ceiling, and
  an unreadable count must not read as "free".
- **Declare the exposure arithmetic before raising a code count.** Codes-per-strategy and total exposure are
  different ceilings: with a total-cost-basis cap of 30% of equity at ~10% per position, "3 of one + 2 of another"
  is 50% and does not fit — the extra slot exists only if per-position size drops (~6% at five names). Tell the
  owner which of the two they are choosing (more names at smaller size, vs more exposure); raising the code count
  alone silently changes per-name size, raising exposure alone changes risk. Compute both from the live caps and
  say the numbers instead of implementing the larger one quietly. Read those caps out of the file on
  every pass — the owner retunes them, and a copy carried in a skill or runbook goes stale (a doc
  claiming "20 % per stock, 10 concurrent codes" coexisted with a live config of 1/3 per stock and 3
  codes, which changes both the sizing decision and whether an order counts as a breach).
- **Prove an untouched live path is untouched, byte for byte.** When a change to a shared producer must not
  disturb a strategy that is already trading: back the published artifact up, regenerate with the change, and
  compare each pre-existing key's **parsed value for deep equality** (not "looks the same"):
  `cp feed.json /tmp/before` → run the producer → compare `before[k] == after[k]` for every old key, dict-diffing
  on mismatch. Report that comparison as the evidence; a new key beside identical old keys is the shape that lets
  a feed change ship mid-session without stopping the running strategies.
- **An unvalidated capability is not a delivered capability.** If the path cannot be exercised end to end before
  the next session (no candidates, no restart yet), say so plainly and name the one remaining step plus who must
  do it — rather than reporting the feature as available.
- **A derived export is still real data: find its generator before deleting anything untracked.** An untracked
  directory (`?? data/` in `git status`) is not evidence of test residue — one held the per-day fills export the
  bookkeeping chain publishes from the journal. Grep for the writer first (`grep -rn "<dir>" tools/ scripts/`);
  if a generator exists, the files are derived-but-real: move them aside instead of `rm -rf`. If they were
  deleted, regenerate day by day with that generator and **reconcile the totals against the journal** (the journal
  is the source of truth, e.g. a day's realised P&L must reproduce), and state that a regenerated file matches in
  substance but may not be byte-identical to the original.

## Data-integrity guards (price and time)

The two inputs every risk gate depends on can lie, and neither failure raises an error by itself:

- **Price: cross-check the exit price against the broker's own mark.** A position response usually
  carries an evaluation amount, making `eval_amount / qty` a second, independent price from the same
  payload. Beyond a small tolerance, take the **worse** price for the side you hold (for a long, the
  lower one: stops still fire, take-profits do not fire early) and log the pair. Without it a
  position can sit far past its stop while the quote the monitor reads still shows a small loss, so
  no stop ever fires and the loss far exceeds what the gate was sized for. When the two disagree, do
  not assume the quote is the honest one: matrix the mark against the quote for **every** position
  (the outlier is the suspect feed) and settle it from a raw quote payload -- previous close, day
  high/low, volume -- before calling it a data bug. A name that gapped up and then closed at its low
  really is far below entry, and the arithmetic belongs in what you report.
- **Time: verify the clock externally, never let it grade itself.** Every session rule (hours, entry
  windows, day rollover, "is this after hours?") is a function of the local clock, and a skewed
  clock makes the whole model wrong while every component agrees with every other. Compare the local
  time against an external timestamp (an HTTP `Date` header is enough), cache the result, **block new
  entries** past a tolerance and **never block exits** -- refusing to sell because a clock check
  failed is worse than selling on a slightly wrong timestamp. An unreachable source is a warning
  unless fail-closed is explicitly wanted, since market data may be local and usable offline.
  Corollary for diagnosis: timestamps logged during a skew cannot tell you whether activity was in
  or out of session -- settle the clock question before drawing that conclusion. A distro/VM clock
  **freezes while the host sleeps and jumps forward on the next call that wakes it**, so a reading
  taken before the sleep still looks current hours into the session: never say "the market is
  closed" / "the book is flat" from the local `date` alone — pair it with the external header in the
  same pass, then read the loop's own last-cycle time before reporting state.
- **Retries: only the "request never went out" class may be repeated.** Retry a transport-level
  failure that the broker itself labels as re-requestable, bounded with backoff; never blanket-retry
  a real remote rejection, because that is how one order becomes two.

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

## Editing the live order path itself

- **Adding a gate to a live engine has three invariants the tests already pin.** (1) The passing
  decision's gate list is often fixed by a test (measured: exactly six gates on a pass), so a new gate
  may append a `GateResult` **only when it blocks** — record the passing observation in a note field
  (`algo_json.edge_gate = "pass"`) instead of a gate entry. (2) Reuse the quote/position read the cycle
  already performs: gates that each fetch their own quotes inflate bridge calls per cycle, and a single
  batched quote request breaks the chunking contract the runner's pre-filter depends on (measured: one
  15-code call made the "every quote request <= 5 codes" test fail) — chunk to the same constant. (3) A
  data-dependent gate fails **open** when its input is missing (no quote ⇒ no spread/spike judgement) and
  says so in the reason, so a dead feed cannot quietly become a market judgement.
  Deploy: kill only the loop pid and let the supervisor relaunch (measured: a new child within one
  second, no manual start), then prove it from the new pid's own start banner plus the first cycle line
  (e.g. `REGIME risk_off: ... 확장 비활성`) — not from the file you edited.
- **Locate the class that owns the call site before inserting a helper.** Inserting at the module's
  first `def` (or any positional guess) can land the method in a *different* class in the same file;
  the call site then faults and, because the new gate is fail-closed, **every entry is refused** —
  measured: one misplaced helper turned the whole suite red (44 failures) with the log reading like a
  market judgement. Grep the file for `^class`, insert inside the class that already holds the gate,
  and only believe it after the project's full suite is green.
- **A sequence-valued config field can be stringified by the config's own coercion layer, silently disabling every
  `state in cfg.states` check.** A dataclass config that rebuilds itself through `as_dict()` +
  `with_overrides()` coerces unknown-typed fields with `str(value)`, so `("risk_off",)` becomes the literal
  string `"('risk_off',)"` and membership tests fail while the value still prints in `status`. Measured: the
  conservative-mode list, the bull-expansion list and the spike-guard list were all inert for hours,
  and the only tell was a reason line reading "not risk_off" beside a state of `risk_off`.
  Fix the coercion (parse tuple/list/set and quoted or comma-joined strings back into a tuple), then
  **assert the type** in a test (`isinstance(cfg.states, tuple)` after a round-trip) and confirm the
  effective value inside the running process — never from the file you edited.
- **A gate change's deploy timing is the owner's convention — read it before touching the live copy.**
  In a session where the owner's standing rule is "no code changes while the market is open", the correct
  shape is: write the patch, tests and spec **in-session** against the repo copy, run the full suite, then
  after the close do `sync → stop → backfill/repair → start → verify from the new process id and its own
  last-cycle line`. Say explicitly in the report which step is prepared-but-not-yet-live, so a prepared
  patch is never read as an applied one. (The mid-session restart below is equally valid when the owner
  wants it — the point is to follow their convention, not yours.)
- **On a red run of the live path, revert first and redo from a written spec.** `git checkout --
  <that one file>` → re-run the suite to prove green → re-apply as a delegated change whose spec names
  the file **and the class/function** to modify. Iterating forward leaves the tree in a state the
  running loop may load; a reverted path is the only safe intermediate state.
- **Scope cleanup and reverts to explicit paths, and read back both repos.** `git checkout -- .` in a
  repo whose pipeline writes tracked state silently rewinds tens of those files to an older snapshot,
  and the working directory you are actually in may not be the one you named: a failed `cd` does not
  stop the *following* lines of a multi-line command, so a delegation can start in the previous repo
  and leave strays there. Pin the cwd on the invocation itself (`cd <abs> && pwd && ls <known file>`)
  and check `git status` in both the intended repo and the previous one after any cleanup.
- **A close row's timestamp may be the exit convention, not the fill.** A journal `exit_ts` written as
  a *future* session boundary (measured: a Thursday exit stamped `next session 09:02`) silently matches
  windowed audits that filter on that date, so an old close is reported as today's. Take exit times for
  audits from the broker's fills export, and queue a boundary-shaped or future `exit_ts` as a defect
  instead of reading it as a rounding artifact.
- **A refused entry response can still be recorded as a held position.** Rows carrying `order_no: 0`
  plus a rejection message (`거부`-class text) sit in the journal as trades that were later held and
  closed. Keep such a response `rejected`/`unknown` in the journal's own state machine: a row that
  reads as filled while the broker never accepted the order is the same defect class as a phantom fill,
  and it corrupts every downstream expectancy number.
- **One order per code per cycle, and prove it against the broker's working-order list.** The
  "already entered today" guard reads local bookkeeping, and every plan in a batch is built from one
  account snapshot taken before the batch, so a candidate list carrying the same code twice places two
  live orders while the journal records one — a broker/journal quantity mismatch that also breaches the
  per-stock cap when both fill (measured: two identical 1-share limit buys 0.5 s apart against one row).
  Deduplicate codes within the batch *and* read the broker's own working orders before each order; a
  failed preflight is fail-closed, because a missed entry is cheap and a duplicate is not. The same
  order returns by a second path — a **process restart**: a supervisor relaunch starts with an empty
  pending set and re-derives the plan, so the guard has to live in the broker read, never in local
  memory. To undo a duplicate, cancel one order rather than trading into it, and confirm from the
  working-order list (a cancel answers with an echo, not with state; the request also needs the
  instrument code beside the order id — a missing field is refused as invalid input).
- **Queue live-path defects as protected tasks, not as auto-merge candidates.** File the finding with
  its measured evidence (row, prices, timestamps) and mark it protected so the autonomous loop produces
  a proposal plus a human-review request rather than editing the order path unattended.

## Reporting to the owner

Lead with what is running (process + health output) and what changed, then the measured numbers with
how they were obtained. **For a closed trade, the journal's exit price is what the monitor saw, not the
fill**: partial fills leave the broker's per-code average sell below (or above) the journal's `exit_price`
(measured: journal +389 KRW vs broker average +126 KRW on the same 2-share round trip). Quote the broker's
average for money and say the journal figure is the trigger price, and report the day's realised total from
the broker's own daily-PnL payload; a journal whose `fees` column is 0 makes journal PnL gross, which is
another reason the two disagree.

**Verify a post-hoc "you sold too early" claim against post-exit prices before conceding anything.**
The owner reacts to the index, not to each name: on a day the index rallied from -5.5% to -2.4%, two of the
three names already exited were *below* their exit price (further falls avoided) and only one had run on.
Read the current and the day's high for each exited code, compute the missed/avoided amount per name, and
report it per name with the exit rule that fired — then fix only the rule that actually cost money (a
90-minute time exit cutting a winner, not the stop). Conceding a whole day of exits because the index
recovered is how the wrong rule gets loosened.

**Reading the live journal from the other OS: copy first, query second.** A WAL-mode sqlite file owned by
the running Windows process answers `sqlite3.OperationalError: disk I/O error` to a WSL reader (drvfs
locking), which looks like a corrupt database. Copy the triple — `<db>`, `<db>-wal`, `<db>-shm` — into a
temp dir and query the copy; never checkpoint or open the original read-write just to look. Label estimates as estimates. When something is blocked on a human step, say
which step — an unattended agent that reports a stale or derived number as a live result is worse than
one that reports "cannot read this right now, here is the last measurement and why".

**Never re-type a number you did not just read from the source.** Reconstructing plan prices, quotes or
returns from an earlier print or from memory is how a fabricated data error reaches the owner (measured:
two hand-copied plan prices disagreed with the published payload, and the report claimed the producer's
data was off by +340 % — the payload was correct, and the retraction costs more than the read would have).
Pull every per-item figure with a program read in the same pass that reports it. **Cumulative totals (fills ingested, rows in a table, realised P&L to date) come from the table, never from a job's log tail** — a pipeline log's last summary line belongs to the run that wrote it, so a tail read can quote an interim count while the table already holds more (measured: a tail said 17 fills / −33,652 while the table held 21 / −34,755). Count with a query, and when the two disagree say which is the table and which is the log. And before calling a
producer's data stale, read the producer's own schedule and ask what the last *completed* input was at
that moment: a daily batch that runs pre-close legitimately carries the previous session's signal the
next morning (a holiday week stretches that to several days), so "it never refreshed" is usually a
misdiagnosis of a correct producer — check the job's own log for its last run time before saying it.

When the owner asks an open "what should we do now?", answer with (1) the measured current state as
of right now, (2) the next scheduled event and who must act on it (human step vs agent), then (3)
numbered options with an explicit recommendation — a survey of possibilities is a worse answer than a
recommended next action. When the ask is a pre-event checklist ("what has to happen before the open /
before we start"), lead with the ONE step that blocks everything, how many minutes until it expires,
and what it costs to miss that deadline; then the numbered verification steps in the order they must
run, each with the observation that proves it.

**An approval that names no item is not an approval — enumerate the candidates before acting.** "승인할게"
/ "go ahead then" arriving while several things are pending resolves nothing by itself, and one of the
candidates can be an irreversible real-money action (market orders, a cap change, a promotion). Read the
pending set from where the system itself records it (the scoreboard's/roles' `needs_human` list, your own
last reported options) and ask **one** question that lists the concrete items with their consequences; put
the action the user most plausibly means first. Never fire a live order on an ambiguous approval, and do
not read a batch instruction ("keep watching X") as carrying approval of a trading action you happened to
list earlier in the same message. Once approved, state which item you are executing in the same turn you
place it, so the transcript pairs the order with its authority.

A mid-session "how is it going?" has a fixed shape too: (1) the loop and link state in one line, (2)
what it filled since the open — code, the rule that fired (stop / take-profit), entry to exit price and
P&L, with one clause on why it fired, (3) current holdings with the mark, the move against entry, and the **distance to each position's own
stop**, computed from the journal's recorded stop width and high-water mark rather than recomputed by
hand — the line is `max(entry × (1 − stop_pct), hwm × (1 − trail))`, the trail term counting only once
the row's own `hwm_price` armed it (`hwm >= entry × (1 + arm)`) — **with arm/trail resolved from the live config, never from a hardcoded copy
inside the reporting tool**, because the pair is tuned from measured exits and a stale copy states a
confident distance to the wrong line (grep the reporting tools for the previous pair whenever the knob
moves, then re-run one and confirm the printed line changed) — and `current` being the *lower* of the
live quote and the broker's own `eval_amount / qty` mark — a holding inside ~1% of its trigger is what
the *next* cycle will actually do, and it deserves
its own short paragraph (including the case where that position's stop model predates the model now in
use, so the exit about to fire is the old setting, not the current one), 
(4) the day's realised total **on its own line** — never presented as (equity now − day-start equity),
because realised P&L is measured against the entry price while the day-start snapshot carries pre-open
marks, so the two baselines differ and the gap is not an error, (5) the entry count and, when it is
zero, the *measured* reason (a recomputed filter, not "the market is quiet"), then (6) numbered options
in the owner's language.

Answer "is it running right now?" from the OS and the wire, never from the state file alone: the
process list (with the broker terminal's own process), one link-health probe, and **a second cycle
observed** (the log grew by one cycle interval) — a halted, stale or day-closed loop still leaves a
perfectly plausible state file behind. Read that process list from the side that owns the processes: a
Windows-side loop and bridge appear in the Windows task list and never in the distro's `ps`, so an empty
`ps` is not evidence of a stopped run.

**A cumulative-return ask is an equity question, not a journal sum.** Report the broker's own equity
(cash + securities evaluation) against the account's starting capital and break it down — realised
(closed trades) plus unrealised (open cost basis vs current evaluation) — instead of forcing the two
to agree. The residual has known, nameable causes: commission/tax on sells is missing from a journal
whose fee column is 0 (derive it per code **only for a name bought and sold in the same session** —
§5 of `references/broker-vs-journal-reconciliation.md`; a position carried over from an earlier session has
no same-day buy, so the broker's per-code realised figure reads 0 and the subtraction returns the whole
gross as "cost". Then report the gross, say net is unresolved, and leave the column alone rather than
supplying a rate — and **verify the broker figure's unit before deriving anything from it**: a per-code realised P&L that arrives truncated to thousands (2,233 KRW reported as `2`) makes every derived cost noise, so a fee backfill whose dry run flags *every* row as negative or oversized cost is a **unit mismatch, not bad data** — do not apply it (a wrong cost column is worse than a zero one); get an exact-unit source (a fills/체결내역 export, or one large trade) first), a broker `cash` field that means *orderable* cash rather than settled deposit
does not close a cash identity, and the broker's evaluation price basis differs from your last quote
snapshot. Headline the broker number, label the components, and name the gap as an open item (a
reconciliation pass, not a mystery) — but compute every term of the identity from **one** source
first: a journal plan-price cost basis stirred into the broker's cash figure manufactures a gap that
is your arithmetic, not the account's, and publishing it costs a retraction. Recipe:
`references/live-status-and-pnl-reporting.md`.

