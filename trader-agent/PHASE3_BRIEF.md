# PHASE 3 BRIEF — trader-core (Windows 64-bit Python, signal sourcing + risk + journal)

## Mission

Implement `trader_core/` inside this repo: the **64-bit decision/risk core** of the Creon agent
trader. It runs on the Windows laptop (64-bit Python 3.12, `C:\Users\jhshi\Python312-64`),
alongside the existing 32-bit creon-bridge (`bridge/`, already done in Phase 2). trader-core
NEVER touches Creon COM directly — it talks to the bridge only over localhost HTTP
(127.0.0.1:8100, exact contract in `bridge/server.py` + `PHASE2_BRIEF.md`), and it sources
trading candidates/signals by polling the BC-250 screener feed over HTTP.

This phase is the LIBRARY + ENGINE layer. Phase 4 will wrap a Hermes agent around it
(operating loop, kill-switch orchestration, in-market automation). Do not build a 24/7
autonomous daemon; build correct, tested building blocks plus a CLI for humans to drive.

## Architecture context (authoritative: /home/dduckbeagy/AUTONOMY/CREON_TRADER_PLAN.md §4-7)

```
[Windows laptop]
  32bit Python  -> bridge/ (Creon COM)  -> localhost HTTP :8100
  64bit Python  -> trader_core/ (THIS PHASE) + Hermes agent (Phase 4)
        ^ polls BC-250 screener feed (tailscale HTTP)
[BC-250]  screener-http :8080 serving ~/screener_export/screener_latest.json
```

Data contract of the BC-250 feed (real sample from 2026-09-03, all values are STRINGS):

```json
{ "generated_at": "2026-09-03T21:54:09.825334+09:00", "source": "bc250_analyist_dd",
  "candidates": { "close":  {"count": 20, "file": "...", "items": [ { "rank", "stock_code", "stock_name",
      "sector", "signal_date", "close_price", "score", "volume_surge", "close_strength",
      "ret_3d_pct", "ret_5d_pct", "day_change_pct", "short_ratio", "reason", ... } ] },
    "swing": { ...same shape..., extra: "kalman_trend","kalman_slope","noise_resid_std","volatility_ann","model_prob" },
    "daytrading": { ...same shape... } },
  "scoring_summary": { "generated_at", "total_scored", "skipped_existing", "screener_stats": {...} } }
```

- Feed URL default: `http://100.127.186.48:8080/screener_latest.json`
  (BC-250 tailscale IP; screener_export dir is served at web root). Configurable via env/CLI.
- `scoring_summary.screener_stats` may be EMPTY `{}` (observed) — Kelly prior must fall back to
  config defaults without error. Values are strings; parse defensively.
- `signal_date` can lag behind `generated_at`; judge freshness on `generated_at`.

## Reference material (READ ONLY — never modify)

1. `/home/dduckbeagy/AUTONOMY/CREON_TRADER_PLAN.md` — the single design authority (this brief
   is its §4-8 distilled into executable spec).
2. Existing bridge code IN THIS REPO — read `bridge/server.py` (exact endpoint + response-key
   contract), `bridge/orders.py`, `run_bridge.py`. trader-core's HTTP client MUST match these
   keys/verbs exactly (derive fixture shapes from this code, not guesses).
3. BC-250 legacy assets to PORT from (read, adapt, vendor — do NOT import; the laptop will not
   have these paths). Attached as `-f` files:
   - `order_sizer.py` — `KellySizer` (f* = p − q/b; cap semantics used there)
   - `position_sizer.py` (strategy-agents) — `calculate_kelly_fraction`
   - `stop_loss.py` (strategy-agents) — StopLoss: default SL 7% / TP 15%, check/trailing/vol/time logic
   - `balance_checker.py`, `position_checker.py` (trade-executor risk_management) — balance/position
     limit validation semantics (their Redis/DB storage is NOT portable — replace with config-driven
     in-memory/local checks)
   - `mock_creon_executor.py` — pattern for a deterministic mock order executor (used in tests/dry-run)
4. The old `trade-executor` notion of market-order reference-price validation ("CWE-20 방어"):
   a market order must be rejected unless a recent reference price exists (e.g. last quote) or a
   sanity band is enforced. Port this guard into the risk layer.

## Non-negotiable constraints

1. Target runtime: **Windows 64-bit Python 3.12**; code must also run unmodified on Linux
   `python3` (BC-250 dev). Use **stdlib only** (`urllib.request`, `http.server`, `sqlite3`,
   `threading`, `dataclasses`, `json`, `datetime`, `argparse`, `unittest`). NO requests, numpy,
   pandas, pywin32, Redis clients. Python ≥3.10 syntax level (mind `datetime.fromisoformat` tz
   handling: feed timestamps carry `+09:00`).
2. **No PyQt/Qt/UI. No COM.** No import of anything under `/home/dduckbeagy/analyist_dd`.
   All ported risk logic must be self-contained pure functions/classes.
3. All prices/quantities in KRW-scaled units as the bridge uses them (6-digit codes, qty in
   shares). trader-core normalizes codes to plain 6-digit ("005930"); the bridge handles the
   "A"-prefix internally — never send prefixed codes to the bridge.
4. Never auto-execute real orders as a side effect of import/CLI defaults: every order path goes
   through the engine gates; a `--dry-run` mode (or mock executor) must be the default for any
   "scan/decide" invocation. Kill-switch file (configurable path): presence ⇒ engine refuses new
   orders and emits a liquidation plan.
5. Fail closed on stale/malformed feed: if `generated_at` older than `feed_max_age`
   (default 6h, configurable) or payload unparseable → gate blocks new entries with a clear reason.
6. English docstrings/type hints, PEP8, ASCII-safe source, small testable modules. unittest only
   (no pytest) — consistent with `bridge/tests`.

## Deliverables (create under repo root)

1. `trader_core/` package:
   - `config.py` — `Config` dataclass + env/CLI overrides: bridge_base_url, feed_url,
     feed_max_age, market hours (KST 09:00–15:30, Mon–Fri; gate on by default with override),
     risk defaults: stop_loss_pct=0.07, take_profit_pct=0.15, kelly_cap=0.25, kelly_fraction
     (fraction of f* to deploy, default ≤0.25), default win-rate prior & avg-win/avg-loss priors
     (used when feed stats absent), daily_max_trades, max_position_pct (per stock, of equity),
     max_order_amount, market-order price sanity band pct (default ±10%), kill_switch_path,
     journal_path (default `journal/trade_journal.sqlite3`), screener names, dry_run flag.
   - `feed.py` — `ScreenerFeedClient.fetch()` → typed `FeedSnapshot`: parses candidates per
     screener into dataclasses (`Candidate`: rank, stock_code, stock_name, sector, signal_date,
     close_price, score, reason, plus optional extras dict), keeps `generated_at` (timezone-aware),
     `is_fresh(now, max_age)`, and a `scoring_stats` dict per screener (empty-safe). Robust
     number parsing (strings → float/int, "" → None). Code validated 6-digit.
   - `bridge_client.py` — `BridgeClient` over the exact bridge contract:
     GET /health /balance /positions /orders(미체결) /quotes · POST /order {side:"buy"|"sell",
     code, qty, price?, order_type:"limit"|"market"} · POST /cancel {order_no, code, qty?} ·
     POST /subscribe {codes} · /unsubscribe · /shutdown. Every call returns parsed dict or raises
     typed `BridgeError` on transport failure; `{ok:false, error:{code,message,...}}` → typed
     exception carrying code+message+action. Timeouts on every request (default 5s).
   - `risk.py` — pure risk math ported from the assets above:
     - `kelly_fraction(win_rate, avg_win, avg_loss)` → f* (0 ≤ f*; q=1−p; guard b≤0, p≤0, p≥1)
     - `KellySizer`-equivalent: `sized_qty(equity, price, win_rate, avg_win, avg_loss, kelly_fraction,
       kelly_cap=0.25, max_position_pct, max_order_amount)` → (qty, invest_amount) respecting
       all caps; qty ≥1 else 0.
     - stop-loss/take-profit checks (`should_stop(position, price, sl_pct, tp_pct)` →
       "stop"|"tp"|None) + trailing-stop helper ported from asset 3.
     - Balance/position guards: `check_buyable(equity, buy_amount, reserve)`,
       `check_position_limit(held_qty, existing_qty, additional, max_position_pct, equity, price)`,
       `check_daily_trade_count(count, daily_max)`, `validate_market_order(price, reference_price,
       band_pct)` (CWE-20 guard).
   - `engine.py` — `TraderEngine`: the gate pipeline. Inputs: BridgeClient, ScreenerFeedClient,
     Config, optional Journal. `scan()` = fetch feed → `evaluate(snapshot)` returns a
     `DecisionReport` with per-candidate `Decision`s; each Decision carries gate results:
     (a) feed fresh? (b) kill-switch clear? (c) market hours? (d) daily trade count < max?
     (e) balance/position/order-amount caps pass? (f) signal sane (score>min, price>0)? Only when
     ALL gates pass does the decision carry an order plan: `OrderPlan(side, code, qty, price?,
     order_type)` sized via Kelly from equity + scoring_stats win-rate (fallback priors).
     `execute(plan)` routes through the bridge (or mock/dry-run executor) and journals it.
     Liquidation helper: `liquidation_plans(positions, reason)` → sell plans for all holdings
     (used by kill-switch/monitor). Order plans for 'close'/'swing'/'daytrading' are gate-checked
     by the same pipeline with screener-specific config (e.g. daytrading requires market hours +
     fresh intraday data when present).
   - `monitor.py` — `PositionMonitor.heartbeat()`: pull `/positions` + `/quotes`, evaluate
     stop/tp per open position vs current price, return list of `ExitSignal(code, side="sell",
     reason="stop"|"tp", qty, order_type="market")`. Handles missing quotes (no data → hold,
     note in status, do not fabricate).
   - `journal.py` — SQLite journal (stdlib sqlite3, WAL): tables `decisions` (ts, screener, code,
     name, score, gates_json, plan_json, reason) and `trades` (id, ts, side, code, name, qty,
     price, order_type, order_resp_json, entry_reason, stop_pct, tp_pct, kelly_f, exit_ts,
     exit_price, exit_reason, pnl, fees=0). Methods: `record_decision`, `record_trade`,
     `close_trade`, `summary(period_days)` → {trades, wins, losses, win_rate, avg_win, avg_loss,
     total_pnl} for the Kelly prior feedback loop (Phase 5). Schema migration via
     `PRAGMA user_version` (v1). Journal path configurable; `:memory:` supported for tests.
   - `killswitch.py` (or in config/engine) — `kill_switch_active(path)` file-existence check.
   - `__init__.py` exporting the public surface.
2. `run_trader_core.py` (repo root, mirroring `run_bridge.py` style) — CLI entry:
   - `status` — bridge /health + feed freshness + equity/positions (read-only)
   - `scan [--dry-run] [--screener close,swing,daytrading]` — fetch feed → engine evaluate →
     print DecisionReport; default dry-run
   - `close-all [--reason ...]` — liquidation plans for open positions (still routed via bridge;
     prints plans and asks confirmation unless `--yes`)
   - `--mock` flag: talk to an in-process MockBridge (deterministic fills per mock_creon_executor
     pattern) instead of the real bridge — used for laptop dry tests and e2e tests.
   - `--config path.json` optional; env overrides `TRADER_CORE_*`.
   - Exit codes: 0 ok, 2 usage, 3 gate-blocked (scan found nothing actionable), 1 runtime error.
3. `trader_core/tests/` — unittest suite, all green on Linux `python3` with NO pywin32, NO
   network (fixtures + in-process mock HTTP server):
   - feed parse: real-shape fixture (string-typed values, empty screener_stats, stale
     generated_at → is_fresh False, malformed → GateBlock)
   - risk math: kelly formula + edge cases (p≥1, b≤0, caps, qty rounding to 0), stop/tp triggers,
     market-order CWE-20 guard (reject when no reference / out-of-band)
   - engine: all-gates-pass → plan; each gate fails → blocked with reason; kill-switch file
     toggling; stale feed blocks; daily-count cap; liquidation plans
   - bridge client: against an in-process `http.server` fake that mimics the real bridge
     response shapes (derive keys from `bridge/server.py`/`bridge/orders.py`); error path
     `{ok:false, error:{code,...}}` raises typed error; timeout/refused → BridgeError
   - journal: record/close/summary round-trip on `:memory:`; migration idempotence
   - e2e dry-run: engine + MockBridge + fixture feed → decisions recorded, no real network touched

## Verification (run in repo; iterate until clean)

```
python3 -m compileall -q trader_core run_trader_core.py
python3 -m unittest discover -s trader_core/tests -v
python3 run_trader_core.py status --mock      # smoke: should exit 0 and print status
python3 run_trader_core.py scan --dry-run --mock   # smoke: exit 3 or 0 with printed report
```

Report: files created, test counts (tests/assertions), exact formulas + caps implemented,
response-key contract you derived from `bridge/` for fixtures, gate list order, and any
assumption/ambiguity you resolved (flag it).

## House rules

- Only create/edit: `trader_core/**`, `run_trader_core.py`, `PHASE3_BRIEF.md` (if you must fix a
  typo). Do NOT touch `bridge/`, `run_bridge.py`, `creon_src/`, `creon_extracted_v1/`,
  `README.md`, `PHASE2_BRIEF.md`, or git history. Do not commit; the orchestrator commits.
- Do not add third-party dependencies or pip-install anything.
- Keep modules small; pure functions where possible; every public function/class typed+docstringed.
