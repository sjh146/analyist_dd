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
  alone silently changes per-name size, raising exposure alone changes risk. Changing the code count also means
  changing the equal-weight denominator (`max_position_pct = 1/N`) and the sizing/slot tests **in the same commit**:
  the suite pins the old fraction and a bare cap change turns it red with no behaviour change, which then reads
  like a regression. Compute both from the live caps and
  say the numbers instead of implementing the larger one quietly. Read those caps out of the file on
 every pass — the owner retunes them, and a copy carried in a skill or runbook goes stale (a doc
 claiming "20 % per stock, 10 concurrent codes" coexisted with a live config of 1/3 per stock and 3
 codes, which changes both the sizing decision and whether an order counts as a breach).
 **The live config has more than one writer.** Another agent session can commit a risk cut into it
 mid-session, and its message may cite an owner approval you were not part of (measured: a 5-entry
 config became 2 entries / 30 % exposure / 2 slots between two of your own reads, justified by a
 measured −1,212 KRW per-trade expectancy). Before writing caps back, run
 `git log --oneline -5 -- <config>` and diff; if the newer commit is a documented risk reduction, do
 not silently revert it — restate both value sets and ask, and offer the middle option that keeps the
 cut but relaxes only the filter that would starve data collection (a high entry-edge floor is the
 usual one: it cuts candidate count to a third while doing nothing for per-trade risk). Restoring
 your own older numbers over another session's approved cut is a money decision, not a merge.
- **Prove an untouched live path is untouched, byte for byte.** When a change to a shared producer must not
  disturb a strategy that is already trading: back the published artifact up, regenerate with the change, and
  compare each pre-existing key's **parsed value for deep equality** (not "looks the same"):
  `cp feed.json /tmp/before` → run the producer → compare `before[k] == after[k]` for every old key, dict-diffing
  on mismatch. Report that comparison as the evidence; a new key beside identical old keys is the shape that lets
  a feed change ship mid-session without stopping the running strategies.
- **A candidate's "today" is only as fresh as its own last bar — date the signal and gate the bar's age.**
  A producer that stamps `signal_date` as the reference trading date while reading a stock's *last available*
  bar will publish months-old prices as today's signal, and every consumer-side freshness check passes because
  the payload is new (measured: three names whose data ended in Jan–Aug were published as same-day candidates).
  Two guards, both at the producer: (1) the stamp is the **reference trade date**, not the stock's own last row;
  (2) drop candidates whose last bar is older than N days (e.g. 5) from the reference date and log the count
  plus the codes. Otherwise the trader fills at a stale price that no risk gate can detect.
- **A published artefact is delivered only if a scheduled step pushes it to the repo the consumer pulls.**
  The feed can exist locally with the right counts while the remote branch still carries yesterday's — or only
  a code release (measured: local publish succeeded, remote head was a code commit from the previous night, and
  the publish lived behind a CI path nobody exercised). So: (1) clone/symlink the *consumer's* repo and add the
  publish call to the scheduler's own wrapper, not only to CI; (2) open the wrapper — a trailing `exec` of the
  first step silently makes later steps unreachable; (3) verify from the **remote**: `git fetch` then
  `git log origin/<branch> -1` plus the artefact's `sha256sum`, and hand the consumer that exact hash+size to
  compare. A code repo is usually the wrong target for data — check `git ls-files <data path>` before telling the
  owner which repo to clone. If the path cannot be exercised end to end before
  the next session (no candidates, no restart yet), say so plainly and name the one remaining step plus who must
  do it — rather than reporting the feature as available.
- **A published artefact the serving process cannot read is not delivered — and the failure looks like a dead
  service, not a permission.** A feed published by a root cron lands `root:root 0600`; the server that serves it
  runs as another user, fails every read, and resets the connection (`curl` → `000`, `Invoke-WebRequest` →
  "connection closed") while the process is alive and the port is LISTENing. Judge delivery by the **consumer's
  own port** (`curl --noproxy '*' 127.0.0.1:<port>/<path>` → HTTP 200 plus a byte count), never by process liveness
  or the file's mtime, and make every publish path end with `chmod 644 <artefact>` so the next scheduled run cannot
  re-break it. Symptom to recognise: "the trader stopped getting candidates" with a healthy-looking stack.
- **A producer run without its output flag is a no-op, and every gate's input must be published.** Re-running a
  screener in the foreground without its output flag (`--json-out <path>`) writes nothing: the source artifact keeps
  its old mtime and the publisher re-emits *yesterday's* candidates, so a correct patch looks like a patch that did
  not apply. Verify a producer change by the artifact, never the exit code — file mtime moved **and** the new key is
  present in the parsed payload. When one payload has two serializers (CSV rows and the feed-source JSON), the field
  must be added to both: patching only the CSV builder leaves the feed unchanged with every test green. Publish the
  number the gate itself uses (`trading_value` beside a liquidity floor) or the gate cannot be audited downstream,
  and the audit then reports a phantom violation ("all candidates below the threshold") for a payload that merely
  lacks the field.
- **A derived export is still real data: find its generator before deleting anything untracked.** An untracked
  directory (`?? data/` in `git status`) is not evidence of test residue — one held the per-day fills export the
  bookkeeping chain publishes from the journal. Grep for the writer first (`grep -rn "<dir>" tools/ scripts/`);
  if a generator exists, the files are derived-but-real: move them aside instead of `rm -rf`. If they were
  deleted, regenerate day by day with that generator and **reconcile the totals against the journal** (the journal
  is the source of truth, e.g. a day's realised P&L must reproduce), and state that a regenerated file matches in
  substance but may not be byte-identical to the original.
- **Turning a dormant strategy path on takes four gates, and the last one silently locks the path out.** In order:
  (1) the consumer's `screeners` list, (2) the per-path slot cap, (3) the per-path **capital** cap, (4) the per-path
  **entry threshold**. Then count, from the *published payload*, how many candidates actually pass gate 4 — the
  answer is not the candidate count. Measured: 20/20 swing candidates carried `edge_score` 1 while the global
  `min_edge_score` was 4, so opening slots and capital produced exactly zero entries and the log looked like a
  quiet market. A per-path threshold must be **absolute**: if the global value gets a regime bonus added
  (`+2` under `risk_off`), the effective bar rises above every candidate the path will ever see. Resolve the
  override verbatim (no bear/regime bonus, no bull relief) and keep the global rule untouched for paths that
  do not override.
- **Two independent daily entry caps exist; the effective limit is the min, so raise both or neither.** The engine
  enforces `daily_max_trades` (plus a bull-expansion bonus) while the runner enforces its own `r2_max_entries_per_day`
  (a hard entry gate evaluated before the engine). Measured live: engine 50 (+50 in `risk_on`) but runner 10, so
  the day's real ceiling was 10 and the "50–100 entries" setting never applied. When quoting a daily limit, load
  both layers (`Config.from_file` for the engine, the runner config for R2), print both numbers and state which
  one binds. Per-profile `max_entries_per_day` in the profile table is usually **not** wired to any gate — grep
  for its consumers before counting it as a cap.
  The used-count is **derived from today's journal/broker fills, not from the loop's state file**: rewriting
  `loop_state.json` → `daily.entries` leaves the ceiling exactly where it was (the gate keeps reading "entries
  today N >= max M" from the journal), so the only ways to run a same-day test window are to raise the caps for
  the test entries — and restore them immediately, reporting the deviation — or to wait for the next session.
  Prove which one you are bound by from the running process's own skip reason (`daily trade count N >= max M`
  vs `r2: entries today N >= max M`), never from the state file you edited.
- **A capital split between paths is arithmetic the config must state explicitly.** `max_invested_pct_by_screener`
  (`{path: fraction}`, sum ≤ 1) caps each path's cost basis at `equity × fraction − (journal cost of open
  positions on that path + plans already made this cycle)`, applied **after** the risk-off size scaling and as a
  `min()` with the per-stock and global caps; remaining ≤ 0 must block with a reason naming the path cap rather
  than planning a 0-share order. An empty dict must reproduce the old sizing bit-for-bit — pin that with a test
  before enabling, because the regression is silent (positions just get smaller). Also raise the *global* code cap
  to at least the sum of the per-path slots, or the global cap binds first and the split never materialises
  (`swing 2 + daytrading 3` needs `max_open_positions ≥ 5`). Verify from a loaded config object, not the JSON file:
  print each path's budget, slots and per-name size for the account's actual equity.

## Data-integrity guards (price and time)

- **An unreadable position response is not a flat book.** Any audit that diffs journal against broker must carry an
  explicit `positions_known` flag and only judge a row "missing at the broker" when `/positions` actually answered.
  On a read failure the state is `unverified` and the mismatch verdict is withheld — measured: a dropped broker link
  produced a high-severity broker↔journal mismatch every 5 minutes for a name the broker still held, burying the one
  real signal (the link was down). Apply the same rule to the daily-PnL read: report "unreadable", never 0, and let
  the flat-book conclusion come from a successful read alone.

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
  a real remote rejection, because that is how one order becomes two. A plain client-side **timeout**
  belongs to neither class — the request may have reached the broker and filled — so read the working
  orders and positions before reporting a failure or resubmitting (opening-minute timeouts measured as
  `order failed` while the fills were live at the broker).
- **Resolve an order timeout into a journal row instead of leaving it as "order failed".** If the fill happened
  and nothing is journaled, the position exists only at the broker: it inherits the **global** stop rather than
  the path's (measured: a daytrading entry managed at the 7% global stop instead of its 1%), drops out of the
  per-path slot and quote-set unions, and the audit reports broker-without-journal every cycle — measured as 70
  minutes of unmanaged holding on a single name. Branch four ways from a fresh broker read: held ⇒ journal a
  recovered entry priced at the broker's average, carrying the plan's own stop/tp and path attribution; working
  order only ⇒ journal it pending(working); nothing at the broker ⇒ "order not accepted"; broker unreadable ⇒
  "order result unknown". No branch may re-place the order inside the same cycle.

- 상세: `references/before-going-live.md` — Before going live

- 상세: `references/overnight-carry.md` — Overnight carry and the next open

- 상세: `references/live-path-self-audit.md` — 불변식 자동 감사 루프(브로커↔저널 불일치, 손절 미집행, 미체결 주문제존, 유동성 위반)와 일일 개선 큐 설계

## Editing the live order path itself

- **An algorithm change is not done until it has traded once in the session, as intended.** The owner's standing rule:
  "알고리즘 변경했으면 의도한 대로 잘 매매되는지 장중에 테스트해봐야 해". So every change ships with a live
  observation plan written before the restart — the concrete line to look for (order price vs the cycle quote,
  cancel of an unfilled order after N s, the gate reason naming the new filter) and the number that proves it
  (entry price within one tick of `current`, not the feed's `close_price`). A green unit suite plus a config that
  loads is evidence about the code, not about the order path. When the day's entry budget is already spent, either
  wait for the next session or declare a bounded test window (raise the cap by the number of test entries, then put
  it back) and report that deviation explicitly — never quietly exceed the owner's daily limit.

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
- **A loop that outlives its session keeps the config it loaded at start; outside the supervisor's window
  nothing will restart it into the new file.** A watchdog that only (re)launches inside a time window
  (e.g. 08:30–15:35) leaves a live loop running after the close, idling on yesterday's thresholds — and
  because it is healthy, the next morning's start is a no-op and the changed config never takes effect.
  After any post-close config edit: confirm the supervisor's own log line reads `outside … - loop not
  started`, stop the surviving loop, and verify from the process list that no `run --loop` process remains,
  so the next session starts fresh with the file on disk. When quoting limits the same day, print them from
  a freshly loaded config object, not from the running process — the process is the stale one.
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
- **A raised scoring weight can be inert against a hardcoded clamp.** Measured: a component's weight was
  raised to 40 in the weights block while the same component's clamp still capped it at 25, so the new
  weight was unreachable and the ranking never changed — while the constant printed as applied. Whenever a
  weight or a threshold moves, grep the module for a numeric bound on that same component (`min(`/`max(`,
  `clamp`, a `*_CAP` constant) and raise it in the same commit; then prove the effective value from a
  scored row (the component's contribution, not the constant). A "the tuning had no effect" impression is
  usually this, not noise.
- **A fixed-percent trail is a bug on high-ATR names; scale it and keep `arm ≥ trail`.** A 1% tick trail on
  names with 5–7% ATR exits on noise: measured, a position that peaked +1.45% closed at +0.13% and then ran to
  +2.1%. Read the candidate's ATR from the trade's own record (`algo_json.candidate.atr_pct`) and use
  `trail = max(base, 0.35 × ATR%)` capped (e.g. 6%), then set `arm = max(arm, trail)` so the wide trail cannot
  trigger below entry. Fall back to the base value when ATR is absent (fail-open) and pin both branches with tests.
- **An engine with two exit paths needs the exit change in both, and the slow one is the one that costs money.**
  The same knob is often resolved twice: a fast tick watcher and the periodic monitor each compute their own
  trail/stop from the profile. A fix applied to the tick path runs only while ticks arrive, so the slower path
  still exits on the old fixed percentage and the journal shows the old reason on exactly the positions the fix
  was meant to protect (measured: ATR scaling landed in the tick watcher alone; the monitor kept closing
  high-ATR names at the flat 0.8 % trail). Grep for every reader of the knob before declaring the change done,
  call the same helper from both, and prove it from the exit reason line of a position closed by the *slower*
  path — not from the file you edited or from the fast path's log.
- **An entry filter needs both bounds; the chase cap does not stop a falling knife.** A book whose only same-day bound
  is an upper one (`max_entry_day_change_pct` = +15%) still buys names already down 8–9% on the day — measured: an
  entry filled mid-crash several percent below the previous close and stopped out below that. Add a floor
  (`min_entry_day_change_pct`, e.g. −1%, 0 = disabled) computed from the same quote (`current − diff` is the previous
  close), scoped to the paths it should govern, fail-open when the quote carries no `diff`, and pin three cases:
  below-floor blocks with its own gate name, a mild dip passes, 0 disables. Quote the floor with the rest of the
  path's parameters — the owner reads it as strategy, not as plumbing.
- **A day-scoped risk relaxation belongs in a dated override file, not in the code defaults.** Editing
  `min_edge_score` / spread caps in place leaves the aggressive value in the repo after the day that justified
  it. Put the temporary values in a file with `valid_until` (applied only while today ≤ that date, unknown keys
  ignored) and restore the conservative defaults, with tests for today-applies / expired-ignored / unknown-key
  ignored. Restart the loop to load it, and verify from the running process's own log line (the effective
  threshold in a skip reason), not from the file you edited.
- **Relocating the live code directory moves the order path: the scheduled tasks are the dependency that goes stale.**
  A move that leaves task actions pointing at the old path produces a loop that never starts — with every config
  file, journal and log looking correct at the new location. Order: end the supervisor tasks first (a watchdog
  restarts the loop otherwise), kill any surviving python processes (a running process holds the directory and
  the move fails), move the tree, rewrite every task's `Actions.Execute`/`Arguments` (`Set-ScheduledTask`) for
  both the launcher and its supervisor, then prove it from the new path with the app's own status command
  (`run_market_loop.py status --config <cfg>`) and, if a bridge is involved, the `cwd=` line in the bridge's
  own log. While verifying, read the status header's `screeners` value too: a default-looking list means the
  status call ran without `--config` and did not load the live settings. Finish by grepping the repo for the
  old absolute path and fixing the diagnostic scripts that hardcode it.
- **Queue live-path defects as protected tasks, not as auto-merge candidates.** File the finding with
  its measured evidence (row, prices, timestamps) and mark it protected so the autonomous loop produces
  a proposal plus a human-review request rather than editing the order path unattended.

## Order pricing, unfilled orders and holdings outside the feed

Four defects that look like "the strategy is bad" but are order-path mechanics, measured on one live book:

- **Never price an entry from the published feed's close; use the live quote.** The candidate's `close_price`
  is the *previous session's* close. A limit at that price fills only when the market trades back through it,
  i.e. exactly on the names that are falling — measured: entries filled 3–5 min after the signal at a price the
  stock had already left, then stopped out below it. The user's own words for this are "왜 시장가보다 비싸게 사서
  싸게 파는거야": treat that as an order-pricing bug report, not a market-comment. Resolve the order price from the
  cycle's quote (`current`, falling back to `price`), keep the limit type, and re-check `stop_pct`/TP sizing with
  the same price so the gates and the order share one number.
- **An unfilled entry order must be cancelled on a timer, not left working all session.** A limit that misses is
  logged `FILL NOT CONFIRMED (working: nothing filled)` and, with no intraday cancel, can fill hours later at a
  price nobody chose. Cancel working buys after a short bound (order of a minute) and journal the cancel; a
  fill that arrives after the bound is a position with no plan behind it. Verify from the broker's own
  `/orders` list (`filled_qty`, `cancelable_qty`), not from the placement response.
- **The cycle's quote set must be candidates ∪ journal open codes.** Quotes are usually gathered from the current
  candidate list only; any holding that has dropped out of the feed (adopted at reconcile, or a path whose feed
  was cleared by a gate) then reads `no current quote; holding` in the monitor and **its stop can never fire**
  (measured: a name ran to −6 % against a −1 % stop while the log repeated that line every cycle). Union the open
  codes into the quote request each cycle — chunk to the same constant — and prove it by exiting an off-feed name.
- **A connectivity/check halt must gate entries only, never the exit path.** A loop that returns before its monitor
  phase while halted leaves open positions unmanaged: measured, the broker link dropped, the loop halted, and a
  position sat **5.5 h past its 90-minute max-hold** with no exit attempt because every cycle short-circuited at the
  halt check. Restructure so a halt **with open positions** still runs the monitor/liquidation path and says so in
  its reason line; keep only the non-resumable halts (daily-loss limit, kill-switch file) as full stops. Verify the
  behaviour from the next cycle's own phase/reason line, not from the state file — an auto-resume that never fires
  looks identical to a cleared halt until you watch one cycle run.
- **An order-pricing guard must be asymmetric by side: refusing a BUY on a stale quote is cheap, refusing a SELL is a
  stuck position.** A "recent reference quote required" guard re-fetched its own quote, timed out on that second read
  while the cycle's quote was healthy, and refused the same market exit on every cycle for hours. For sells only, fall
  back to the quote the plan already carries from this cycle and journal which source produced the reference
  (`live_quote` | `cycle_quote`); keep buys on the strict path, because an entry priced off a stale reference is the
  defect the guard exists for while a delayed exit is the defect it creates.
- **Exit orders are usually market orders, so the trigger price is not the fill — measure the book before changing the order type.**
  Measured on one book: trailing/stop exits filled 0.35–0.82 % worse than the trigger the journal recorded, and the
  trigger was then written into the journal as if it were the fill. Swapping market exits for limit exits to "fix"
  this is a trade you cannot evaluate yet and a stuck limit sell blocks the exit (sellable quantity 0, no retry).
  Two steps in this order: (1) record the book at trigger time (`current`, `bid1`, `offer1`, spread) inside the
  trade's own record so the counterfactual is measurable from real quotes, then (2) only if the numbers justify it,
  move to a marketable limit at the best bid **with** a watchdog that cancels and re-sends market if it does not
  fill within a bounded time. Never leave a limit sell working with no fallback, and never report the trigger price
  as the exit price.
- **Close the trigger-vs-fill gap by correcting the journal, not by explaining it in a report.** When market
  exits keep filling worse than the trigger, run a post-close tool that overwrites **today's closed rows'**
  `entry_price`/`exit_price` with the broker's per-code averages (print before→after per row), then add an audit
  check that flags any row where journal exit deviates from the broker average sell by more than ~0.3 % —
  escalate the whole class to a scheduled job (e.g. 15:45) so tomorrow's stats start clean. A journal that
  overstates P&L corrupts every expectancy/win-rate judgement made from it (measured: 4 of 5 exits off by
  0.35–3.0 %, roughly 3,000 KRW of phantom profit). The same correction applies to reconciled/adopted rows.
- **Fill the path-attribution column on adopted rows too.** Any column used for per-path slots/attribution
  (`screener`, profile name, strategy) must be explicitly set when a row is adopted from the broker; leaving it
  empty drops that row out of every per-path statistic while the total still looks right (measured: two adopted
  trades carried `""`). Set a distinct value (`"reconcile"`) and cross-check that the per-path sums equal the total.
- **Closing a thin-book position by hand: cross the spread deliberately and say so.** On an illiquid name a
  market order can sit unfilled for minutes (no bid), and a limit placed far below the last price fills but at a
  self-inflicted loss (measured: 1,840 against a 1,853 last → 15주 ≈ 195원 of pure slippage). Place the limit at
  or just inside the best bid, watch one fill, and report the difference from the last price as the operator's
  own slippage — never as the strategy's. The cancel call also needs **both** `order_no` and `code`; an
  `order_no`-only request is refused and the still-working order keeps the sellable quantity at 0.

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

**Per-stock returns are quoted net of fees and tax, every time.** The owner's standing ask is "수수료·세금까지 합쳐서
종목별 수익률": commission on both sides plus the sell-side tax, with the components shown
(`수수료 / 세금 / 순손익 / 순수익률`) and the rates named, not a gross price change. Compute cost basis × mark in one
program read; when the journal's `fees` column is 0 every journal-derived figure is gross, so label it gross or
convert — never present gross as the net number.

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

