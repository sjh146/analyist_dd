# INTEGRATION_MAP.md — trader-core + runner: how it works today, and where self-generated candidates / self-iteration plug in

Scope: read-only analysis of the 64-bit trading core (`trader_core/`) and the
market-hours runner (`runner/`), plus the bridge HTTP contract they depend on
(`bridge/`), as of the code on disk. Written while the bridge is logged into a
모의투자 (paper) account, equity ≈ 5,000,000 KRW, no positions.

Method note: every claim below is "read in code" unless explicitly tagged
**INFERRED**. Line numbers are `file:line` against the current tree. This
document does not change any file; the parent agent implements the changes.

---

## 0. Data-flow in one line

```
candidate source (feed_client.fetch → FeedSnapshot)
        │
        ▼
TraderEngine.evaluate()  ── 6 ordered gates ──▶ Decision.plan (OrderPlan) or None
        │                                            │
        │ journal.record_decision()                  ▼
        │                                   MarketLoop R1/R2/R3/R5 filters
        │                                            │
        │                                            ▼
        │                            TraderEngine.execute(plan) → bridge /order
        │                                            │
        ▼                                            ▼
   SQLite journal ◀──────── journal.record_trade()   creon-bridge (COM) → 모의투자
```

Two independent gate layers exist: the engine's six gates (`engine.py`) and
the runner's R1–R5 rules (`runner/rules.py` + `loop.py`). A candidate must pass
both to reach a live order.

---

## 1. `trader_core/feed.py` — the candidate/screener JSON contract

**Shape (read in code).** Top level must be a JSON object (`feed.py:150-151`):

```json
{
  "generated_at": "2026-09-04T19:29:24.300622+09:00",   // REQUIRED, ISO-8601, tz-aware
  "source": "bc250_analyist_dd",                        // optional, defaults ""
  "candidates": {                                       // REQUIRED, must be an object
    "<screener>": {                                     // e.g. "close"|"swing"|"daytrading"
      "count": 20, "file": "...csv",                    // ignored by the parser
      "items": [ { ...candidate... } ]                  // REQUIRED to be a list, else []
    }
  },
  "scoring_summary": {                                  // optional
    "screener_stats": { "<screener>": { "win_rate": "60.0",
        "avg_win_pct": "...", "avg_loss_pct": "..." } } // may be {} — must not crash
  }
}
```

- `FeedSnapshot.from_payload` raises `FeedError` when the payload is not a dict
  (`feed.py:150-151`), when `candidates` is not an object (`feed.py:152-154`),
  when `generated_at` is missing/unparseable (`feed.py:155`, `85-101`).
- Anything else degrades: a screener container whose `items` is not a list
  becomes an empty list (`feed.py:159-162`); a non-dict item is skipped
  (`feed.py:164-166`); an item with no valid 6-digit `stock_code` is dropped
  (`feed.py:198-200`).
- `scoring_summary.screener_stats` that is `{}` or malformed is absorbed as an
  empty dict (`feed.py:172-177`).

**Per-candidate fields (read in code).** `Candidate` dataclass
(`feed.py:104-116`). Core keys are lifted by `_CLOSE_CORE`
(`feed.py:31-36`); **every value in the sample feed is a string** — numbers are
coerced by `_parse_float`/`_parse_int` (`feed.py:43-70`), empty strings → `None`.

| field | type after parse | required? | notes |
|---|---|---|---|
| `stock_code` | `str` (6 digits) | **required** | `A`-prefixed form also accepted, prefix stripped (`feed.py:73-82`) |
| `rank` | `int \| None` | optional | `_parse_int` |
| `stock_name` | `str` (default `""`) | optional | |
| `sector` | `str` (default `""`) | optional | |
| `signal_date` | `str \| None` | optional | kept as raw string, not a date |
| `close_price` | `float \| None` | **effectively required** | `None` ⇒ sizing fails ⇒ gate 5 blocks (`engine.py:467-470`) |
| `score` | `float \| None` | **effectively required** | `None` ⇒ gate 6 blocks (`engine.py:557-559`) |
| `reason` | `str` (default `""`) | optional | truncated to 200 chars in the plan (`engine.py:546`) |
| everything else | raw value in `extras` | optional | keys not in `_CLOSE_CORE` and not starting with `_` (`feed.py:202-206`) — this is where `volume_surge`, `kalman_trend`, … ride |

**Freshness (`feed_max_age`).** `FeedSnapshot.is_fresh(now, max_age)`
(`feed.py:129-137`): `age = now - generated_at`; fresh when
`age.total_seconds() <= max_age`. A naive `now` is treated as KST; a
future-stamped payload (negative age) counts as fresh. Default
`feed_max_age = 24*3600` s (`config.py:73`), deliberately covering the
overnight gap between the ~19:30 KST evening export and the next 09:00 open
(`config.py:70-73`). Enforced twice: in the engine gate 1 (`engine.py:266`,
`402-414`) and again in the runner CHECK (`loop.py:400-403`).

**Fetch + error path.** `ScreenerFeedClient.fetch` (`feed.py:237-251`): if the
URL has no `://` and `accept_local_path` (default True) it reads a local file
(`feed.py:242-243`, `253-260`) — this is how tests and offline scans work.
Otherwise `urllib.request.urlopen`; `URLError`/`OSError` → `FeedError`
(`feed.py:247-250`). `run_trader_core.py scan` catches the engine's
`report.feed_error` and exits `EXIT_GATE_BLOCKED = 3` (`run_trader_core.py:206-208`).
`TraderEngine.scan` wraps `fetch()` in a bare `except Exception` and returns a
blocked `DecisionReport(feed_fresh=False, feed_error=str(exc))` (`engine.py:246-250`).
The loop treats any fetch failure as a CHECK failure (`loop.py:396-398`).

---

## 2. `trader_core/engine.py` — candidate → accepted order

**Gate tuple (canonical order)** `engine.py:40-47`:
`feed_fresh → kill_switch → market_hours → daily_trade_count →
balance_position_amount → signal_sane`.

`evaluate()` (`engine.py:254-306`) computes the cycle-level facts once
(`feed_fresh` 266, `kill_active` 267, `market_open` 268-270, `daily_count` from
`journal.trade_count_today` 271-276), then iterates
`config.screener_names` × candidates (`engine.py:279-280`) and calls
`_evaluate_candidate` (`engine.py:386-581`). **A decision is journaled for every
candidate evaluated, pass or block** (`engine.py:293-294`, `_journal_decision`
`661-680`).

Per-candidate, in order:

1. **feed_fresh** (`engine.py:402-414`) — false ⇒ detail `feed is stale (generated_at=…)`.
2. **kill_switch** (`engine.py:415-425`) — fails when the kill-switch file exists;
   detail is its first non-empty line.
3. **market_hours** (`engine.py:426-443`) — `needs_open = config.market_hours_only
   OR (screener == "daytrading" AND config.daytrading_requires_market_open)`.
   Session = KST Mon–Fri `[09:00, 15:30)`, half-open (`engine.py:200-218`).
   Note the non-daytrading screeners can be allowed outside hours by setting
   `market_hours_only=False`, but a `daytrading` screener is *always* gated on a
   live session.
4. **daily_trade_count** (`engine.py:444-462`) — `risk.check_daily_trade_count`
   (`risk.py:234-238`): passes when `count < daily_max_trades`. Count comes from
   journal rows whose entry `ts` is on today's KST day (`journal.py:247-257`),
   or 0 when there is no journal (`engine.py:271-276`).
5. **balance_position_amount** (`engine.py:464-552`) — the sizing + money gate:
   - no usable price ⇒ block (`467-470`).
   - `risk.sized_qty(...)` → `(qty, invest)` (`472-482`); the deployed fraction is
     `f_eff = min(f_star * kelly_fraction, kelly_cap, max_position_pct)`
     (`483-488`; `risk.py:56-105`).
   - `qty <= 0` ⇒ "Kelly size rounds to zero shares" (`489-494`).
   - `risk.check_buyable(account.cash, invest, reserve=0.0)` (`495-504`) — **fails
     closed when cash is 0**.
   - `risk.check_position_limit(held, committed, additional, max_position_pct,
     equity, price)` (`505-521`) — combined `held + committed + qty` value must not
     exceed `max_position_pct * equity`.
   - `invest > config.max_order_amount` (`522-531`).
   - all pass ⇒ `OrderPlan(side="buy", price=candidate.close_price,
     order_type=config.order_type, entry_reason=reason[:200], kelly_f=f_eff,
     stop_pct=config.stop_loss_pct, tp_pct=config.take_profit_pct)`
     (`540-552`). **Price comes straight from the feed's `close_price`** — there is
     no bid/ask/live-price lookup at plan time.
6. **signal_sane** (`engine.py:554-563`) — `score > config.min_score` **strictly**
   (`557-559`) and `price > 0` (`560-562`).

Finally `if not all(gate.ok): plan = None` (`engine.py:568-569`). So a decision is
`passed` iff *every* gate passed.

**Execute / `OrderBlockedError`** (`engine.py:311-353`):
- `dry_run AND isinstance(bridge, BridgeClient)` ⇒ returns
  `{"dry_run": True, "plan": …, "message": …}` with **no bridge call**
  (`engine.py:319-324`). ⚠️ **When the bridge is a `MockBridge`, dry_run does NOT
  suppress**: orders execute against the in-process ledger. That asymmetry is
  intentional (mock == simulation) and is the cheapest iteration harness.
- `order_type == "market"` ⇒ needs a recent reference price via
  `_reference_price` (`642-659`); missing/≤0 ⇒ `OrderBlockedError` (CWE-20)
  (`engine.py:327-333`).
- else a **limit** order needs a positive `plan.price` ⇒ `OrderBlockedError`
  otherwise (`engine.py:334-339`).
- `bridge.order(...)`; `BridgeError` re-raises untouched (`349-350`); the fill is
  journaled by `_journal_fill` (`352`, `682-719`). A **buy** → `record_trade`; a
  **sell** → `close_trade` on the latest open row for that code, else a new sell
  row (`699-719`).

`OrderBlockedError` (`engine.py:50-51`) is the only engine-defined exception.
Bridge failures surface as `BridgeError` (`bridge_client.py:46-77`).

**What makes a candidate skipped (no plan):** any single failing gate. The
runner can additionally skip it at R1 (whole screener), R2 (entries capped), R3
(price moved vs live quote), R5 (news veto) — see §6.

**Account pull** (`engine.py:616-640`): `bridge.balance()` + `positions()` +
`open_orders()`. `cash` = `balance["cash"]` else `balance["withdrawable"]` else
**0.0 (fail-closed ⇒ buys refused)**; `equity` = `balance["equity"]` else
`cash + total_eval_amount`. This 0-cash default is the single biggest live-trading
risk item (§8).

---

## 3. `trader_core/risk.py` — every configurable risk rule

`risk.py` is pure math (no IO, no globals — `risk.py:14-15`). Config keys live in
`trader_core/config.py:66-113`.

| rule (function) | config key(s) | default | scope | what it blocks |
|---|---|---|---|---|
| Kelly size `kelly_fraction` (`risk.py:36-53`) | `kelly_fraction` (`config.py:90`), `kelly_cap` (`config.py:89`) | 0.25 / 0.25 | per-order | `f* = p − (1−p)/b`, `b = avg_win/avg_loss`; guards `p∉(0,1)`, `avg_win≤0`, `avg_loss≤0` ⇒ 0.0 |
| `sized_qty` (`risk.py:56-105`) | `kelly_fraction`, `kelly_cap`, `max_position_pct` (`config.py:97`), `max_order_amount` (`config.py:98`) | cap 0.25 / pos 0.20 / order 10,000,000 KRW | per-order | budget = `equity · min(f*·deploy, cap, max_position_pct, max_order_amount)`; floors to whole shares; `<1` share ⇒ `(0,0)` |
| `check_daily_trade_count` (`risk.py:234-238`) | `daily_max_trades` (`config.py:96`) | 100 | **per-day / portfolio** | today's executions ≥ cap |
| `check_buyable` (`risk.py:183-198`) | buy amount vs *cash*; `reserve` param is passed as `0.0` by the engine (`engine.py:495`) | — | per-order | `amount ≤ cash`; fails closed on non-positive amount/cash |
| `check_position_limit` (`risk.py:201-231`) | `max_position_pct` | 0.20 | **portfolio (per stock)** | `(held + committed + additional)·price ≤ max_position_pct·equity`; fails closed when price/equity unreadable |
| `max_order_amount` cap | `max_order_amount` | 10,000,000 KRW | per-order | not a risk function — enforced inline (`engine.py:522-531`) |
| `should_stop` (`risk.py:137-163`) | `stop_loss_pct` (`config.py:87`), `take_profit_pct` (`config.py:88`) | 0.07 / 0.07 | per-position | returns `"stop"`/`"tp"`/`None` from entry (`avg_price`/`avg_buy_price`/…) vs live price; fail-open (missing entry/price ⇒ hold) |
| `check_trailing_stop` (`risk.py:166-177`) | `trail_pct` param | 0.07 | per-position | **read in code: never called** — no caller for `check_trailing_stop` in the tree; trailing stops are implemented-but-unwired |
| `validate_market_order` (`risk.py:244-274`) | `market_order_band_pct` (`config.py:99`) | 0.10 | per-order | **read in code: never called** — `engine.execute` enforces CWE-20 via `_reference_price` directly (`engine.py:326-333`), not via this band check. `market_order_band_pct` is therefore inert today |
| `market_hours_only` / `daytrading_requires_market_open` | `config.py:79`, `84` | True / True | per-cycle | engine gate 3 |

**Portfolio-level:** `daily_max_trades`, `max_position_pct` (per stock across
orders). **Per-order:** Kelly sizing, `max_order_amount`, buyable cash,
market-order reference guard. **Per-day:** `daily_max_trades`, plus the runner's
`r2_max_entries_per_day` and `r4_loss_limit_pct` (see §6).

---

## 4. `monitor.py` + `killswitch.py` — halts and liquidation

**PositionMonitor** (`monitor.py:56-160`). `heartbeat(now)` pulls
`bridge.positions()` and `bridge.quotes(codes)` (`75-95`) and per position:
needs a usable code, a positive entry `avg_price`, positive `sellable_qty`
(else `qty`), and a positive live `current` quote — any missing ⇒ **HOLD** with a
note, never a fabricated price (`101-114`). Levels come from the journal's open
trade for that code (`stop_pct`/`tp_pct` recorded at entry), else config defaults
(`_levels` `148-160`). Verdict from `risk.should_stop` → `ExitSignal(reason="stop"|"tp",
qty, order_type="market")` (`120-139`).

The monitor only **emits signals**; the runner turns each into a market sell
`OrderPlan` and calls `engine.execute` directly — **exits bypass the entry gate
pipeline** (documented `monitor.py:8-10`; wired `loop.py:624-646`).

**Kill switch** (`killswitch.py:14-39`) is a *file*, default `kill_switch.txt`
(`config.py:111`). `kill_switch_active(path)` = `os.path.exists(path)`, and
**fails closed** (`path` falsy or unreadable ⇒ True) (`14-25`).
`kill_switch_reason(path)` = first non-empty line (`28-39`). It is only ever
*read* by the engine/monitor; the runner **writes** it on the R4 daily-loss
breach (`loop.py:695-708`).

**Triggers:**

| trigger | effect | evidence |
|---|---|---|
| `kill_switch.txt` present | engine refuses new orders (gate 2); runner also skips the whole cycle via `_halted()` | `engine.py:415-425`; `loop.py:277-288` |
| R4 daily-loss breach | liquidate **all** positions at market, set the soft HALT flag, **write** `kill_switch.txt` | `loop.py:649-668`, `670-693`, `695-708`; `r4_triggered` `rules.py:132-147` |
| `max_consecutive_check_failures` reached | auto-HALT (state file only, **no liquidation**) | `loop.py:405-421` |
| manual `run_market_loop.py halt` / `resume` | set/clear the HALT flag in `loop_state.json` (kill-switch file untouched) | `loop.py:261-275`; `run_market_loop.py:271-288` |

**State persistence.** Two files.
- Soft state → `loop_state.json` via `StateStore` (atomic temp-file +
  `os.replace`, `fsync`, a `threading.Lock`): `state.py:71-92`. Shape at
  `state.py:19-34` (`loop.halt`, `daily.*`, `check.*`, `last_cycle`,
  `last_summary`, `updated_at`). Re-read every cycle so an external HALT/resume
  is honoured immediately (`loop.py:198-200`, `283-288`).
- Hard state → the kill-switch **file** (`loop.py:695-708`): a daily-loss halt
  stays hard until an operator deletes the file, because the engine refuses
  orders while it exists.
- Journal (SQLite) → the durable record of decisions/trades (§5).

---

## 5. `trader_core/journal.py` — record format, location, read-back

SQLite (stdlib `sqlite3`), WAL, `PRAGMA user_version = 1` (`journal.py:26`,
`85-97`). Default path `journal/trade_journal.sqlite3` **relative to CWD**
(`config.py:25`), i.e. the repo root when the CLIs are launched as documented
(`schedule.bat.example:26-27`).

Two tables (`journal.py:28-64`):

- **`decisions`** — one row per gate verdict. Columns: `id, ts, screener, code,
  name, score, gates_json, plan_json, reason`. Written by `record_decision`
  (`112-139`), where the engine passes the full gate list as JSON and
  `reason = blocked_reason() or "ok"` (`engine.py:661-680`).
- **`trades`** — executed fills. Columns: `id, ts, side, code, name, qty, price,
  order_type, order_resp_json, entry_reason, stop_pct, tp_pct, kelly_f, exit_ts,
  exit_price, exit_reason, pnl, fees` (`journal.py:41-60`). `record_trade`
  (`151-187`) inserts the entry; `close_trade` (`189-229`) **updates** that row
  with `exit_*` and computes `pnl = (exit − entry)·qty − fees` (inverted for a
  `sell`-sided entry). Indexes on `trades(code, exit_ts)` and `decisions(ts)`
  (`62-63`).

**Read-back hooks already present (for a review step):**
- `recent_decisions(limit)` — newest-first gate verdicts (`141-146`).
- `open_trades()` / `open_trade_for(code)` — unclosed positions (`231-245`).
- `trade_count_today(now)` — today's entry count for the daily cap (`247-257`).
- `summary(period_days=30, now)` — closed-trade aggregates:
  `{trades, wins, losses, win_rate, avg_win, avg_loss, total_pnl}` where
  `avg_win`/`avg_loss` are mean **KRW PnL magnitudes** (`259-288`).

A review step therefore has everything it needs to read performance back — but
**nothing in the tree consumes these** for tuning (§8c). The `summary()` docstring
calls this "the Kelly feedback loop" (`journal.py:5-7`), yet `engine._prior_stats`
reads win/avg stats from the **feed snapshot**, not the journal (`engine.py:586-614`).

---

## 6. `runner/` — schedule, rules, state, CLI

### 6.1 Schedule / interval (`loop.py`)

- Active window = `market_open − start_before_open_min` through
  `market_close + end_after_close_min`, **weekdays only** (weekend ⇒ `closed`)
  (`_schedule_mode` `304-324`).
- `run_once` = `CHECK → DECIDE → ACT → MONITOR` when active; `idle` before the
  window; `closed` after it (`191-233`). CLOSE cancels outstanding orders and
  writes the daily summary (`713-773`).
- `run_forever` sleeps `interval_seconds` between cycles (default **300 s**,
  `config.py:95`); while `idle` it shortens the sleep toward the window start to
  `≥ 5 s` (`235-259`). It exits on `report.closed` or `report.halted`.
- CHECK (`367-434`) requires, in order: bridge `/health.connected`, a usable
  equity from `/balance`, and a **fresh** feed. A failure increments
  `check.consecutive_failures`; at `max_consecutive_check_failures` (3,
  `config.py:127`) it auto-HALTs. The day's `start_equity` is latched on the first
  successful CHECK (`429-432`) — this is R4's baseline.
- Day rollover resets daily counters and stashes the previous `summary` under
  `last_summary` (`_roll_day` `341-355`).

### 6.2 Rules R1–R5 (`rules.py`, orchestrated in `loop.py`)

| rule | function | config | semantics |
|---|---|---|---|
| **R1** regime proxy | `apply_r1` (`rules.py:53-78`) | `r1_top_n` (10), `r1_min_avg_score` `{close:88.0, swing:75.0}` (`config.py:105-110`), `screeners` | per traded screener with a threshold: average of the top-N candidate scores; if `<` threshold, **block the whole screener** this cycle. Screeners absent from the dict are skipped. |
| **R2** entry cap | `apply_r2` (`rules.py:81-93`) | `r2_max_entries_per_day` (100, `config.py:113`), `r2_one_entry_per_code_per_day` (True) | today's buy-fill count ≥ cap ⇒ **no new entries** (monitoring continues); a code that already got an entry today is skipped regardless of the cap (anti-churn). |
| **R3** intraday deviation | `r3_deviation_ok` / `r3_reason` (`rules.py:96-129`) | `r3_max_deviation_pct` (0.03) | drops a plan whose feed `close_price` differs from the live bridge quote by more than 3%; missing quote **fails closed**. |
| **R4** daily-loss limit | `r4_triggered` (`rules.py:132-147`) | `r4_loss_limit_pct` (0.03, `config.py:115`) | equity `< start_equity·(1−0.03)` ⇒ liquidate all + halt + write kill switch. Baseline missing/≤0 never triggers. |
| **R5** news veto | `gate_blocked_codes` (`rules.py:150-174`) | `news_gate_url`, `news_gate_fail_closed` (True), `news_gate_window_days` (3) (`config.py:122-124`) | per-code veto from the BC-250 news-gate HTTP endpoint; empty URL disables it; unreachable + fail-closed ⇒ drop all new entries. |

R2/R3/R5 gate **entries only**; monitor exits route straight through
`engine.execute` (`loop.py:624-646`).

### 6.3 State file (`state.py`)

`loop_state.json` (default `state_path`, `config.py:130`). Empty shape
`state.py:19-34`: `loop{halt,halt_reason}`, `daily{date,start_equity,entries,
closed_pnl,closed,summary}`, `check{consecutive_failures,last_error}`,
`last_cycle`, `last_summary`, `updated_at`. `read()` is side-effect free and
returns a fresh default on a missing or corrupt file (`54-69`); `write()` is
atomic (`71-92`); `_merge_defaults` guarantees the full nested shape (`95-102`).
The loop writes `last_cycle` after every cycle (`loop.py:360-362`).

### 6.4 `run_market_loop.py` CLI surface (`run_market_loop.py`)

```
run_market_loop.py {status|run|halt|resume}
   --config PATH          JSON config (runner + trader_core field names)   (49-51)
   --mock                 use in-process MockBridge instead of the real bridge (52-55)
   --bridge URL           override bridge base URL                         (56-59)
   --feed URL|PATH        override screener feed                           (60-63)
   --once | --loop        single cycle (default) vs cycle until close      (65-72)
   --dry-run | --live     dry_run store_true default=None; --live=store_false (73-80)
   --interval N           cycle seconds override                           (81-84)
   --yes                  live: skip the interactive confirmation          (85-88)
   --reason TEXT          halt reason (default "manual halt")              (90-93)
```

Exit codes (`run_market_loop.py:32-35`): `0` ok, `1` runtime error /
halt-by-loss, `2` usage (incl. live without authorisation), `3` nothing
actionable (idle / outside market hours with `--once`). Mapped in `_run_run`
(`221-252`): halt whose reason contains "loss" ⇒ 1; `closed` ⇒ 0; `phase=="idle"`
⇒ 3.

**Dry-run / live gating (read in code).**
- Runner default `dry_run = True` (`config.py:133`).
- The `run` path **always resolves to dry-run unless `--live` was passed**:
  `_resolve_config(..., live_guard=True)` forces `dry_run = (args.dry_run is not
  False)` (`run_market_loop.py:123-127`) — a file/env `dry_run: false` cannot leak
  through.
- Going live additionally requires **one** of: `--yes`, an existing `live_ok`
  marker file in CWD (`LIVE_OK_MARKER = "live_ok"`, `37`, checked `136-137`), or
  typing `yes` at the prompt (`_confirm_live` `130-142`). Otherwise `EXIT_USAGE`.
- The engine's own dry-run suppression only applies when the bridge is a real
  `BridgeClient` (`engine.py:319-324`) — do not rely on `--dry-run --mock` to be a
  no-op.
- Scheduled recipe: `schedule.bat.example:13-17` runs
  `run_market_loop.py run --loop --live --yes` daily at 08:55 KST with the 64-bit
  interpreter `C:\Users\jhshi\Python312-64\python.exe`.

`status` (`166-215`) prints loop state, journal summary, and bridge
health/equity/positions; `halt`/`resume` (`271-288`) only touch the JSON flag
(kill-switch file is untouched, and the print says so).

---

## 7. Config layering and every tunable

**Order (both cores):** defaults `<` JSON file `<` environment `<` CLI.
- `trader_core` (engine): `run_trader_core.py:89-107` merges
  `Config().as_dict()` → `Config.from_file` → `Config.env_overrides()` → CLI.
- `runner`: `run_market_loop.py:100-127` merges
  `RunnerConfig().as_dict()` → `from_file` → `env_overrides()` → CLI.
- Coercion is type-directed and ignores unknown keys
  (`config.py:153-171`; `runner/config.py:182-211`). `with_overrides()` returns a
  **copy** with keys replaced — the only in-process mutation path (`config.py:173-175`;
  `runner/config.py:213-215`). There is **no** runtime write-back to a config file.

**Env prefixes:** `TRADER_CORE_` (`config.py:22`) for engine fields,
`RUNNER_` (`runner/config.py:23`) for loop fields. Both are upper-cased field
names, e.g. `TRADER_CORE_FEED_URL`, `TRADER_CORE_DRY_RUN`,
`RUNNER_INTERVAL_SECONDS`, `RUNNER_R2_MAX_ENTRIES_PER_DAY`. In the runner,
`RUNNER_*` wins for overlapping names (`runner/config.py:152-165`). The bridge has
its own `CREON_BRIDGE_HOST` / `CREON_BRIDGE_PORT` / `CREON_BRIDGE_REQUEST_TIMEOUT`
(`bridge/config.py:16-18`), not part of this layering.

**`RunnerConfig.engine_config()`** copies `dry_run`, `journal_path`,
`market_open`, `market_close`, `screener_names` onto the embedded `Config` so the
engine gates and the loop always agree (`runner/config.py:217-230`). Note the
runner's `screeners` default is `["close","swing"]` — **`daytrading` is excluded
from live runs by default** (`runner/config.py:102`) even though the engine's own
default list includes it (`config.py:106-108`).

### Engine knobs a strategy would tune (`trader_core/config.py:66-113`)

| key | default | meaning |
|---|---|---|
| `bridge_base_url` | `http://127.0.0.1:8100` | bridge endpoint |
| `feed_url` | `http://100.127.186.48:8080/screener_latest.json` | external screener feed |
| `feed_max_age` | `86400.0` | freshness window (s) |
| `request_timeout` | `5.0` | outbound HTTP timeout |
| `market_hours_only` | `True` | gate all screeners on the session |
| `market_open` / `market_close` | `09:00` / `15:30` | session window (GATE; also drives the runner schedule) |
| `daytrading_requires_market_open` | `True` | daytrading always session-gated |
| `stop_loss_pct` / `take_profit_pct` | `0.07` / `0.07` | exit levels written into each plan/trade (tp raised 15%→7% on 2026-09-11) |
| `kelly_cap` | `0.25` | absolute deployed-fraction cap |
| `kelly_fraction` | `0.25` | fraction of full-Kelly deployed |
| `default_win_rate` / `default_avg_win` / `default_avg_loss` | `0.55` / `1.0` / `1.0` | priors when the feed has no stats |
| `daily_max_trades` | `100` | per-day execution cap |
| `max_position_pct` | `0.20` | per-stock concentration cap |
| `max_order_amount` | `10_000_000.0` | KRW ceiling per order |
| `market_order_band_pct` | `0.10` | **inert** (see §3) |
| `min_score` | `0.0` | candidate score must be **strictly >** this |
| `order_type` | `limit` | entry order type |
| `screener_names` | `[close, swing, daytrading]` | which feed buckets to evaluate |
| `kill_switch_path` | `kill_switch.txt` | hard-stop file |
| `journal_path` | `journal/trade_journal.sqlite3` | SQLite journal |
| `dry_run` | `True` | suppress real bridge orders |

### Runner knobs (`runner/config.py:94-136`)

`interval_seconds` (300), `market_open`/`market_close` (09:00/15:30),
`start_before_open_min` (5), `end_after_close_min` (10), `screeners`
(`[close, swing]`), `r1_top_n` (10), `r1_min_avg_score` (`{close:88, swing:75}`),
`r2_max_entries_per_day` (100), `r2_one_entry_per_code_per_day` (True), `r3_max_deviation_pct` (0.03), `r4_loss_limit_pct`
(0.03), `news_gate_url`, `news_gate_fail_closed` (True), `news_gate_window_days`
(3), `max_consecutive_check_failures` (3), `state_path` (`loop_state.json`),
`journal_path`, `webhook_url` (`""` = log-only), `dry_run` (True).

---

## 8. Integration points

### (a) Where a self-generated candidate source should feed in

**The seam already exists and is duck-typed.** `TraderEngine.__init__` accepts any
`feed_client` that "exposes `fetch()`" (`engine.py:224-239`), and both call sites
pass an object, not a type:
- `run_trader_core.py:138` — `ScreenerFeedClient(cfg.feed_url, …)` → `TraderEngine`.
- `run_market_loop.py:232` — `ScreenerFeedClient(engine_cfg.feed_url, …)` →
  `MarketLoop`, which stores it as `self.feed_client` and calls `.fetch()` in
  `_check` (`loop.py:396`).

So a **self-generated source is a drop-in replacement** for those two lines: an
object with `fetch() -> FeedSnapshot` (or `-> Candidate`/`FeedSnapshot`
equivalent — the engine needs `snapshot.candidates` and `snapshot.is_fresh`).
Because the engine only ever consumes a `FeedSnapshot` (`evaluate` at
`engine.py:254`), **all six gates keep applying unchanged** as long as the produced
snapshot satisfies:

- `generated_at = now(KST)` so `is_fresh` passes (`feed.py:129-137`; tolerates
  future stamps).
- `candidates` keyed by the exact names in `config.screener_names` (engine reads
  `snapshot.candidates.get(screener, [])`, `engine.py:280`). Simplest choice: emit
  everything under `"close"` (and optionally `"swing"`) so the runner's default
  `screeners=["close","swing"]` and R1 thresholds apply, or add your own screener
  name to both `screener_names` and `r1_min_avg_score`.
- each `Candidate` carries `stock_code` (6 digits), `close_price > 0`, and
  `score > min_score` — otherwise gates 5/6 will block it (`engine.py:467-470`,
  `557-559`).
- optionally `scoring_summary.screener_stats` to override Kelly priors per
  screener (`engine.py:586-614`).

Because plans are priced from `candidate.close_price` (`engine.py:544`) and the
runner's R3 re-checks that against a **live bridge quote** (`loop.py:468-478`),
a self-generated source must produce a `close_price` close to the live price or
R3 will silently drop every plan (3% band by default).

**What is missing to *generate* candidates (all read in code, all absent):**
- The bridge exposes **no ranking / conditional-search endpoint**. `server.py`
  routes are exactly `{/health, /balance, /positions, /orders, /quotes}` +
  `{/order, /cancel, /subscribe, /unsubscribe, /shutdown}` (`bridge/server.py:37-38`,
  dispatch `139-155`). There is no `/rank`, no `/screener`, no OHLC/history call.
- `bridge/plus.py` / `orders.py` wrap only CpCybos/CpTdUtil/CpTd0311/0314/5339/
  6033/6032/New5331A/StockMst/StockCur/CpConclusion (`api_notes.md` §4–§8). The
  catalog flags the *un-implemented* candidate COM classes:
  `CpSysDib.CpSvrNew7043` (상승률 상위 랭킹, catalog §1 lines 25-39),
  `CpSysDib.CssStgList` + `CssStgFind` (HTS 종목검색 / 조건식, catalog §3 lines
  77-83), `CpSysDib.CpMarketWatch(S)` (특징주, catalog §2 lines 57-71). The catalog
  itself proposes the exact shape: "7043 랭킹 결과를 브리지 **`/rank` API로
  노출**하고, 상위 N종목만 StockCur 구독" (catalog line 54-55).
- The 64-bit side has **no universe**: the only code lists reachable over the
  bridge are `/positions` and `/orders`. A self-generated source cannot enumerate
  a market on its own today.

**Concrete plug-in shape (recommended):**
1. Bridge: add a COM wrapper for `CpSvrNew7043` (and optionally `CssStgFind`) in
   `bridge/` + a new route (e.g. `GET /rank`) returning
   `{ok:true, rank:[{code,name,price,change_pct,volume}]}`; add it to
   `_GET_ROUTES` (`server.py:37`) and the dispatch table (`server.py:139-147`).
2. 64-bit: add a `RankFeedClient` exposing `fetch() -> FeedSnapshot` that calls
   `/rank`, maps each row to `Candidate(stock_code=…, close_price=row.price,
   score=…, reason=…, extras=…)`, stamps `generated_at = now_kst()`, and returns a
   `FeedSnapshot`. **No engine change required.** Optionally give it a
   `fetch`-compatible `.fetch_file` fallback for offline tests, mirroring
   `ScreenerFeedClient` (`feed.py:253-260`).
3. Wire it at the two call sites (`run_market_loop.py:232`,
   `run_trader_core.py:138`) behind a config/CLI switch so the external BC-250 feed
   stays the fallback when present.

### (b) What is missing today

**(i) Pick candidates without an external feed — MISSING (must build).**
As above: no bridge ranking/conditional-search endpoint, no bridge history/OHLC,
no code universe on the 64-bit side. Every existing code path assumes
`ScreenerFeedClient` against the BC-250 URL (`config.py:68`). The `feed_url` can
currently only point at a *local pre-generated JSON file* (`feed.py:242-243`,
`run_market_loop.py:60-63`) — that is a test/offline convenience, not self-generation.

**(ii) Place orders on the paper (모의투자) account — PARTIALLY EXISTS.**
- **Exists:** the bridge is the real order path — `POST /order` →
  `BridgeClient.order` (`bridge_client.py:122-140`) → `orders.py` `CpTd0311`
  (`api_notes.md` §5.1), and `schedule.bat.example:28-29` states the bridge only
  ever talks to the simulation account (`goods_flag 01`). `run_market_loop.py run
  --live --yes` is the documented way to arm it (`schedule.bat.example:17`).
- **Gap 1 — cash is the hard blocker.** `TraderEngine._pull_account` defaults
  `cash` to **0.0** when `/balance` lacks a `cash`/`withdrawable` field
  (`engine.py:626-629`), and `risk.check_buyable` rejects a non-positive cash
  (`risk.py:196-198`). ⇒ on a cash-only sim account the `balance_position_amount`
  gate blocks **every** buy (`engine.py:495-504`). The bridge reports `cash` from
  `CpTdNew5331A` header 10 with a `d2_deposit` fallback and `buyable_error`;
  `api_notes.md` §6.2a explicitly warns the buyable query can be *unsupported on
  the simulation server* (`orders.py:313-318`, `362-371`). **Verify live:** whether
  `GET /balance` actually carries a non-zero `cash` on this sim account. If not,
  either fix the bridge cash query or feed `AccountState.cash` from
  `total_eval_amount`/`deposit` — but note that changing this weakens a
  fail-closed guard, so it needs a deliberate decision.
- **Gap 2 — no `live_ok` marker in the tree.** The marker is read
  (`run_market_loop.py:136-137`) but there is no such file in the repo; live runs
  rely on `--yes` or the typed prompt.
- **Gap 3 — fills are fire-and-forget.** `bridge.order` returns the 04311 reply;
  the *definitive* fill arrives asynchronously via the realtime
  `DsCbo1.CpConclusion` callback (`api_notes.md` §5.1 lines 180-191, §7.3). The
  loop's fill detection accepts `status=="filled"` or a non-null `order_no`
  (`loop.py:573-593`) — i.e. it treats an **accepted** order as a fill and journals
  it immediately (`engine.py:351-352`). Real fill reconciliation (via
  `bridge/realtime.py` `executions()`, `api_notes.md` §8) is **not wired into
  trader_core**. For honest paper P&L this matters.
- **Gap 4 — no portfolio-level cash accounting in the loop.** The runner tracks
  `start_equity`, `end_equity`, `pnl` in state (`loop.py:752-758`) but the engine's
  per-order cash check runs against the bridge's live `/balance` each cycle, so
  intra-cycle multiple buys are not debited against each other.

**(iii) Review its own journal to adjust parameters — ENTIRELY ABSENT (must build).**
Evidence (read in code):
- No module in the tree reads the journal to modify any Config. A grep for
  `review|adjust|feedback|learn|optimiz` hits only docstrings
  (`journal.py:5-7`, `api_notes.md`) and `schedule.bat.example`.
- `Config` / `RunnerConfig` are immutable-by-copy: the only mutation is
  `with_overrides()` returning a **new** object (`config.py:173-175`;
  `runner/config.py:213-215`). Nothing constructs a new loop with a tuned config.
- The runner never persists a learned parameter; `loop_state.json` holds only halt
  flag, day counters, last cycle, and the day summary (`state.py:19-34`).
- The **data** for tuning exists (`Journal.summary()` `journal.py:259-288`,
  `recent_decisions()` `141-146`, `open_trades()` `240-245`) — but the engine's
  Kelly priors come from the feed, not the journal: `_prior_stats` reads
  `snapshot.stats_for(screener)` and falls back to static config priors
  (`engine.py:586-614`). **Recommendation:** the smallest honest self-iteration
  loop is to compute `(win_rate, avg_win, avg_loss)` from
  `Journal.summary(period_days)` and feed them into the engine's config priors
  (or, better, into the snapshot's `scoring_summary.screener_stats`) once per day,
  then persist the chosen parameters so the change is auditable. Everything else
  (auto-tuning `min_score`, `r1_min_avg_score`, `r3_max_deviation_pct`, …) is a
  green-field build with no existing hook.

### Summary of exists vs must-build

| capability | status | evidence |
|---|---|---|
| Candidate source plug point (`fetch()`→`FeedSnapshot`) | **exists** (duck-typed) | `engine.py:224-239`, `engine.py:280`, `loop.py:396`, call sites `run_market_loop.py:232` / `run_trader_core.py:138` |
| All 6 engine gates + R1–R5 applied to any snapshot | **exists** | `engine.py:40-47`, `runner/rules.py` |
| Self-generated candidate source (ranking/조건검색) | **missing** | no bridge route (`server.py:37-38`), only CpTd/StockMst wrapped (`api_notes.md` §4-§8) |
| Universe / market-wide enumeration | **missing** | only `/positions`, `/orders` expose codes |
| Paper order placement | **exists** | `bridge_client.py:122-140`, `orders.py` CpTd0311, `schedule.bat.example:17,28-29` |
| Paper account cash visibility for buys | **blocked/uncertain** | `engine.py:626-629`, `risk.py:196-198`, `api_notes.md` §6.2a |
| Real fill reconciliation | **missing** | `loop.py:573-593`; realtime `executions()` unwired |
| Journal read hooks for review | **exist** | `journal.py:141-146, 240-245, 259-288` |
| Journal→parameter feedback / self-iteration | **missing** | no consumer of `summary()`; `with_overrides()` is copy-only |
| Config persistence of tuned params | **missing** | layering is static file/env/CLI only (`run_market_loop.py:100-127`) |
