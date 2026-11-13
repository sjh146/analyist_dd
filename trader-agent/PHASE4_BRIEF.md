# PHASE 4a BRIEF — runner: 장중 운영 루프 (Windows 64-bit Python, 모의 자동 매매)

## Mission

Implement `runner/` inside this repo: the **market-hours operating loop** that drives the
existing 64-bit `trader_core` package (already implemented, Phase 3) against the existing
32-bit creon-bridge (localhost HTTP :8100) to trade the Daishin **simulation (모의) account**
during KST market hours. It runs on the Windows laptop with `C:\Users\jhshi\Python312-64`
alongside `trader_core/`, is started by Windows Task Scheduler, self-terminates at market
close, and journals every decision/fill through `trader_core.journal`.

This phase is the deterministic RULES loop (no LLM). The BC-250 Hermes agent (Phase 4b) will
supervise it remotely via the kill-switch file and `loop_state.json`; this brief only builds
the loop itself plus a small notifier stub.

## Architecture context (authoritative: `PHASE4_DESIGN.md` in this repo — READ ONLY)

Design decisions already taken (do not revisit):
- **D1** loop runs on the laptop locally (bridge is 127.0.0.1-only). No port opening.
- **D2** v1 is rules-only (no LLM anywhere in the loop).
- **D3** orders are automatic within the simulation account, but the CLI's default is
  **dry-run**; `--live` must be explicit.
- Loop cycle 5 min, states `IDLE → CHECK → DECIDE → ACT → MONITOR → (close) CLOSE`.
- Rules R1(후보 분포), R2(당일 진입 3건 제한), R3(전일 종가 ±3% 이탈 SKIP),
  R4(일일 손실 equity 3% → 전량 청산 + 자체 HALT) — exact semantics in §"Rules" below.
- v1 trades the **close and swing** screeners only. `daytrading` stays out of the default
  screener list (needs an intraday feed the loop does not have).

## Reference material (READ ONLY — never modify)

1. `PHASE4_DESIGN.md` — the design authority for this brief.
2. `trader_core/**` + `run_trader_core.py` — import and reuse, do NOT edit:
   - `Config` (dataclass + `from_env`/`from_file`/`with_overrides`, `TRADER_CORE_*` env)
   - `ScreenerFeedClient.fetch()` → `FeedSnapshot` (`candidates` per screener,
     `is_fresh(now, max_age)`, `stats_for`)
   - `BridgeClient` (health/balance/positions/open_orders/quotes/order/cancel/subscribe;
     `BridgeError`/`BridgeResponseError`/`BridgeTransportError`) and `MockBridge` (in-memory
     fake with cash, used by tests/e2e — study its surface, it mirrors BridgeClient)
   - `TraderEngine` (`scan()`/`evaluate()`/`execute()`/`liquidation_plans()`, gate pipeline),
     `PositionMonitor.heartbeat()` → `MonitorReport(exits=[ExitSignal(...)])`
   - `Journal` (`trade_count_today`, `record_decision`, `record_trade`, `close_trade`,
     `open_trade_for`, `summary`, `:memory:` support)
   - `killswitch.kill_switch_active(path)` / `kill_switch_reason(path)`
3. Bridge HTTP contract: `bridge/server.py` (read-only) — the loop only talks to the bridge
   through `trader_core.bridge_client.BridgeClient`.

## Non-negotiable constraints

1. Target runtime Windows 64-bit Python 3.12; must run unmodified on Linux `python3`
   (dev/tests). **stdlib only** (`argparse`, `json`, `datetime`, `time`, `sqlite3` via
   trader_core, `threading`, `unittest`, `urllib`). No requests/numpy/pandas/pywin32.
   Python ≥3.10. KST timezone handling exactly like `trader_core` (`datetime.now(KST)`).
2. Reuse `trader_core` wholesale via `from trader_core import ...`. Do NOT duplicate its
   logic. The loop adds orchestration + the R1–R4 rules only.
3. Never auto-execute real orders implicitly: `--dry-run` is the default; `--live` requires
   an explicit flag AND a state-file guard (see CLI). Every order path still passes the
   trader_core engine gates (the bridge paces order slots itself).
4. Fail safe on missing data: no quote for a candidate ⇒ R3 cannot be verified ⇒ candidate
   is SKIPPED (not traded blind); bridge/feed errors in CHECK ⇒ cycle SKIP with reason,
   N consecutive failures (config, default 3) ⇒ HALT.
5. All paths/files use the laptop's ASCII-safe layout; log/state/journal paths come from
   config with sensible defaults under the repo dir (journal stays opt-in for scans but the
   loop ALWAYS journals — pass a Journal explicitly to engine/monitor and reuse the same
   connection for R4 accounting decisions where applicable).
6. English docstrings/type hints, PEP8, ASCII-safe source, small testable modules,
   `unittest` only.

## Deliverables (create under repo root)

1. `runner/` package:
   - `runner/config.py` — `RunnerConfig` dataclass (own fields + embedded trader_core
     `Config`), layered like trader_core (`RUNNER_*` env, JSON file, CLI overrides).
     Fields: interval_seconds=300, market_open="09:00", market_close="15:30",
     start_before_open_min=5 (schedule window), end_after_close_min=10,
     screeners=["close","swing"], r1_top_n=10, r1_min_avg_score={"close":88.0,"swing":75.0}
     (per-screener thresholds; screener absent from dict ⇒ R1 skipped for it),
     r2_max_entries_per_day=3, r3_max_deviation_pct=0.03, r4_loss_limit_pct=0.03,
     max_consecutive_check_failures=3, state_path (default `loop_state.json`),
     journal_path (default `journal/trade_journal.sqlite3` — the loop always journals),
     webhook_url="" (Discord; empty = notify only to log), dry_run=True.
   - `runner/loop.py` — `MarketLoop` class implementing the state machine. Public:
     `run_once(now=None) -> LoopReport`, `run_forever(now=None)`, `status()`,
     `halt(reason)`, `resume()`, `_halted()` (checks state file HALT flag OR
     `kill_switch_active`). LoopReport dataclass with cycle outcomes
     (checked/decided/acted/exits/halted + reasons list). Semantics:
     - CHECK: bridge `health()` connected + `balance()` ok (cash/equity present),
       feed fetch + fresh, kill-switch file absent, state HALT flag absent,
       journal today's trade count known; failures recorded; consecutive-failure
       counter ⇒ auto-HALT (writes state file with reason, does NOT liquidate — feed/bridge
       trouble is not a market-loss event).
     - DECIDE: run `TraderEngine.evaluate(snapshot, now, account)` restricted to
       `screeners` config; then apply R1 (if any candidate list of a traded screener is
       non-empty and its top-N average score < threshold ⇒ all candidates of that screener
       blocked this cycle), R2 (today's entry count ≥ r2_max_entries_per_day ⇒ no new
       entries; positions still monitored), R3 (per candidate with a plan: fetch live
       quote; if |quote − close_price|/close_price > r3_max_deviation_pct ⇒ drop plan).
       Plans surviving all rules are the cycle's candidates for ACT.
     - ACT: for each surviving plan, `engine.execute(plan)` in dry-run mode
       (`engine.config.dry_run=True` or MockBridge) or live; journal fills happen inside
       engine; R2 counts only successful live/dry fills of buy side.
     - MONITOR: `PositionMonitor.heartbeat(now)`; every ExitSignal → liquidation order via
       engine (market, respects CWE-20); then R4: equity now (from a fresh `/balance`) vs
       `daily.start_equity` (first CHECK of the day, persisted in state file):
       drawdown_pct ≥ r4_loss_limit_pct ⇒ liquidate ALL positions + `halt("daily_loss_limit")`
       + write kill-switch file (so engine also refuses) with reason "daily_loss_limit".
     - CLOSE (after market_close): cancel outstanding orders (`bridge.open_orders()` →
       `cancel`), write daily summary into state file, exit (run_forever returns).
     - Day boundary: persist `daily: {date, start_equity, entries, closed_pnl}` in
       `loop_state.json`; reset when date changes.
   - `runner/notify.py` — `Notifier(webhook_url=None)` with `send(text)` logging always and,
       when url set, POSTing `{"content": text}` to the webhook (urllib, 3s timeout,
       swallow errors to a log line). Provide `build_payload(text)` as a pure function for
       tests.
   - `runner/state.py` — small JSON state-file reader/writer with atomic replace
       (`tmpfile` + `os.replace`), thread-safe enough for a single loop process.
   - `runner/__init__.py` — public exports.
   - `runner/tests/` — unittest (Linux green, no network):
     - state file round-trip + halt/resume flags + atomic write
     - notify: payload shape; no URL ⇒ no HTTP; URL set ⇒ posts (stub urllib via
       monkeypatch) and swallows errors
     - R1/R2/R3/R4 pure decision helpers (extract them as testable functions, e.g.
       `apply_r1(snapshot, cfg)`, `r3_deviation_ok(close_price, quote_price, max_pct)`)
     - loop end-to-end with MockBridge + fixture feed + injected `now`:
       market-open Monday → cycle produces plan(s) in dry-run; kill-switch file present ⇒
       no ACT; HALT flag set ⇒ no DECIDE/ACT; R4 triggered (drop MockBridge cash) ⇒
       liquidation orders + halt + kill file; after market_close ⇒ orders cancelled and
       report closed; day rollover resets daily counters
     - CLI parser tests for defaults (dry_run True) and `--live` requiring the guard.
2. `run_market_loop.py` (repo root, mirrors `run_trader_core.py` style) — CLI:
   - `status` — print loop_state.json (or "no state yet") + journal summary (if journal
     exists) + bridge health (via live bridge unless `--mock`)
   - `run [--once|--loop] [--dry-run|--live] [--interval N] [--config FILE] [--mock]`
     — runs the loop; default `--once` + `--dry-run`. `--live` requires an explicit
     `--live` flag AND prints/requires a one-time confirmation unless `--yes` (or a
     `live_ok` marker file exists); keep it simple but real.
   - `halt [--reason ...]` — write state HALT flag (soft stop; kill-switch file is the
     hard stop used by remote ops).
   - `resume` — clear the HALT flag.
   - Exit codes: 0 ok, 2 usage, 3 nothing actionable (e.g. outside market hours with
     --once), 1 runtime error/halt-by-loss.
   - Note: config from file/env; everything also overridable via flags where sensible.
3. `runner/schedule.bat.example` + comments — Windows Task Scheduler recipe:
   daily 08:55 (KST) run `python run_market_loop.py run --loop --live --yes` under the
   64-bit Python, working dir = repo. Document the schtasks command in a comment block.

## Rules (exact semantics, implement as pure helpers where possible)

- **R1 후보 분포 (regime proxy)**: per traded screener with candidates, take top-N by
  score (N=r1_top_n, default 10; sort desc, numeric score; fewer than N ⇒ all). If
  non-empty and average score < `r1_min_avg_score[screener]` ⇒ every candidate of that
  screener is blocked this cycle with reason "r1: avg score {x:.1f} < {threshold}".
- **R2 진입 제한**: entries counted = buy fills recorded today (journal) — engine dry-run
  fills also count when dry-run (so dry-run pilots exercise the limit). When count ≥
  r2_max_entries_per_day (default 3) ⇒ skip ACT for new entries; monitor still runs.
- **R3 가격 이탈**: planned buy price (feed close_price) vs current quote. Missing quote
  or quote ≤ 0 ⇒ SKIP candidate ("r3: no quote"). Deviation = |q − p| / p > 3% ⇒ SKIP
  ("r3: price moved {x:.1%}").
- **R4 일일 손실**: equity_now < start_equity × (1 − r4_loss_limit_pct) ⇒ liquidate all
  open positions (sell market), halt, write kill-switch file with first line
  "daily_loss_limit". start_equity recorded at the day's first successful CHECK (bridge
  balance equity; absent ⇒ cash + total_eval_amount).

## Verification (run in repo; iterate until clean)

```
python3 -m compileall -q runner run_market_loop.py trader_core
python3 -m unittest discover -s runner/tests -v
python3 -m unittest discover -s trader_core/tests    # regression: must stay green
python3 run_market_loop.py status --mock             # exit 0
python3 run_market_loop.py run --once --mock         # dry-run default; exit 0 or 3 (closed market)
```

Report: files created, test/assertion counts, rule semantics as implemented, which
trader_core APIs you consumed, deviations/assumptions.

## House rules

- Only create/edit: `runner/**`, `run_market_loop.py`, `runner/schedule.bat.example`
  (and `PHASE4_BRIEF.md` only to fix a typo). Do NOT touch `bridge/`, `trader_core/`,
  `run_trader_core.py`, `creon_src/`, `creon_extracted_v1/`, `README.md`,
  `PHASE2_BRIEF.md`, `PHASE3_BRIEF.md`, `PHASE4_DESIGN.md`, `MARKET_DAY_CHECKLIST.md`,
  or git history. Do not commit; the orchestrator commits.
- No third-party dependencies, no pip installs. Keep modules small; pure functions where
  possible; every public function typed + docstringed.
