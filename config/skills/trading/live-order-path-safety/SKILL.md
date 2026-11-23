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

- 상세: `references/invariants.md` — The invariants

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

- 상세: `references/before-going-live.md` — Before going live

- 상세: `references/overnight-carry.md` — Overnight carry and the next open

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
- **Widening profit-taking on a market-direction hunch: verify the premise first, then make the widening conditional.**
  When the owner says "US semis rallied, our market will rise, let winners run", quote the **measured** index moves
  (prior close and the currently running session are different facts — a −10.7% prior close with a +1% live bounce is not
  "rallied") and read the regime artifact before touching exits: under `risk_off` the day's entries are zero, so a wider
  take-profit changes nothing. Say that ordering out loud instead of implementing the exit change as if it were the fix.
  Then implement the widening as a **per-profile pair of conditional knobs** (`trailing_stop_pct_up`, `tp_pct_up`) resolved at
  exit time only when the position's market is above its entry-time level — reuse the existing `market_rose_since_entry(row)`
  check (it reads `algo_json.market_index`) rather than adding a second signal — and leave base values for flat/down days.
  Keep the take-profit stored per trade in the journal (a knob change then affects new entries only) and pin both branches
  with tests: market up ⇒ wide trail/TP, market down ⇒ base values, and a position that never armed behaves as before.
  Unconditional longer holds already measured negative expectancy on this book; widening must not become the default for
  the days the premise is wrong.
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

