---
name: creon-cybosplus-trader-agent
description: Use when automating Creon/Daishin trading via trader-agent.
---

# creon-cybosplus-trader-agent (Daishin Creon CybosPlus automation)

Workspace: `C:/Users/jhshi/analyist_dd/trader-agent` (WSL: `/mnt/c/Users/jhshi/analyist_dd/trader-agent`) — since 2026-09-27 the trader-agent clone lives *nested inside* the analyist_dd clone; the old top-level `C:/Users/jhshi/trader-agent` no longer exists. (Paths written with forward slashes on purpose: the patch tool eats backslash escapes.)
Read `OPERATIONS.md` (runbook), `bridge/api_notes.md` (COM contract + live findings),
`bridge/SIGNALS_NOTES.md` (ranking/특징주/조건검색 recipes), `INTEGRATION_MAP.md` (64-bit core map),
`FEED_CONTRACT.md` (what analysit_dd must send: JSON shape, screener keys, ML results) before
changing code.  For reviewing/changing the *strategy* from live data, use the
`trader-strategy-iteration` skill instead of improvising an analysis.

## Non-negotiables

- **Bitness split.** `run_bridge.py` = 32-bit Python (`C:\Users\jhshi\Python311-32\python.exe`)
  because CybosPlus COM is 32-bit only. `run_trader_core.py` / `run_market_loop.py` = 64-bit
  (`C:\Users\jhshi\Python312-64\python.exe`, deliberately no pywin32). Never import `win32com`
  on 64-bit; never add ML libs to the 32-bit interpreter.
- **Creon PLUS must be running and logged in** (admin/UAC). `run_bridge.py --no-server` printing
  NOT CONNECTED means the HTS is not up — a human step, not a code bug.
- **WSL cannot reach the bridge.** It binds Windows `127.0.0.1:8100`; from WSL go through
  `cmd.exe /c curl ...` or just use the 64-bit Windows interpreter.

## Live-verified COM quirks (do not "fix" these back)

- **Realtime silently dies without the idle pump.** CybosPlus delivers events to the apartment that
  did the Cybos work; a subscription created after any BlockRequest receives zero ticks unless the
  blocking worker pumps messages while idle. That is `bridge/plus.py::pump_waiting_messages`
  (regression: `bridge/tests/test_plus.py`). Symptom: `/subscribe` returns ok, ticks never arrive.
- **Char-code fields arrive as ASCII ints.** 대비부호 `49/50` = `'1'/'2'`, `ex_flag` `50` =
  `'2'` (장중 체결), `goods_flag` came back `"10"` not the documented `"01"`. Normalise with
  `chr()` / `sign_char()`; never compare against `'2'`.
- **Equity = cash + stock evaluation, never CpTd6033 header(3).** Live: header(3) was the stock
  evaluation alone (594,590) while cash was 4,401,227; using it made the core read an ~88 % daily
  loss and would have tripped R4 (liquidate-all + halt). Raw header is exposed as
  `broker_total_eval`.
- **모의투자 limits:** `CpTdNew5331A` (buyable cash) fails with `94007 모의투자 매매불가 종목입니다`
  → `cash` degrades to `d2_deposit`; some instruments are rejected as untradeable.
- **An order response is not a fill confirmation.** `CpTd0311` can answer `order_no=0` with
  `94060 이미 처리되었습니다` and the order still fills completely (37/37 and 34/34 measured); the same
  answer also covers genuinely unfilled orders, and `94059 ... 정상처리 되었습니다` is the same
  non-answer on the sell side. Treat it as "status unknown" and confirm from `CpConclusion`
  (`flag=1`) or `/positions` -- never from the order echo.
- **Entry fills are confirmed at the broker in code now** (`TraderEngine._confirm_entry`,
  `Config.confirm_entry_fills` default ON): right after a buy it reads `/orders` + `/positions` and
  returns `filled|partial|working|unconfirmed` as `result["fill_confirm"]`, and a `partial` journals
  the *broker's* qty, not the planned one. The loop reports `working`/`unconfirmed` as
  `FILL NOT CONFIRMED` + notifies "order(s) placed", records the code in `loop_state.json ->
  pending_entries`, and `_verify_pending_entries` re-checks it every cycle -- confirmed when the
  broker holds it, and a never-filled row is closed at cost (PnL 0) to free its concurrent-position
  slot once it is older than `pending_entry_grace_s` (120 s; a just-filled order vanishes from the
  working list before the position settles). Do not reintroduce "`order_no` is present => filled":
  317830 was announced filled at 11:56 on 2026-09-21 while the broker had only a working order
  (2548, filled_qty 0) -- it filled ~5 min later, and the phantom row held one of the 10 slots.
- **Exit fills are NOT confirmed (entry fills are).** `_journal_fill` closes the row from the order
  response plus the market reference quote, so a sell the paper engine never filled is still booked as a
  clean exit: measured 2026-09-28 13:49, 067630 at 상한가 hit its TP -> a 시장가 매도 (order 2714) sat
  `filled_qty=0` / `sellable_qty=0` while the journal closed the trade at 3,405 with `pnl +18,090`; the
  truth was in `tools/daily_pnl.py` (CpTd6032 = `0건`, `매도실현손익 0`) and in `/positions` (27 shares
  still held). The journal then showed 9 open rows against 10 broker positions, which under-counts the
  동시보유 게이트 and over-states the day's `closed_pnl`. Recover mid-session with
  `Python312-64 tools\reopen_phantom_exit.py --code <code> --yes` (backs the journal up, restores
  exit_*=NULL, keeps the previous exit record in `algo_json.reopened_exit`) and **leave the live
  unfilled order alone** -- fix its price later via `tools\reconcile_journal.py --fix-exit CODE=PRICE`.
  The close reconcile (§8.2) would reopen it anyway, but only after the session.
- **A stock pinned at 상한가 stops producing ticks**, so its `/quotes` timestamp freezes (067630 read
  `time 1345` at 13:52 with `offer1 0`), and the paper account does not fill a 시장가 매도 into the
  standing bid queue. Expect an exit order to sit unfilled there instead of treating it as a code bug.
- **`BlockRequest()` can raise instead of returning.** CybosPlus throws
  `(-2147352567) DibRq 통신요청 실패하였습니다. 다시 요청하십시오.` when its internal request never went
  out; `bridge.plus.block_until_ok` now retries those (3x, 0.3→1.5 s) but *must not* retry
  `BridgeError`/other exceptions. Symptom without it: `/positions` and `/balance` answer
  `server_error` for minutes and the loop cannot value anything.
- **The realtime cache is not a price source for exits.** `/quotes` with codes does a live
  `StockMst`, yet a position was once marked at 6,050 while the broker's own mark was 5,040, so a
  -17.8 % holding sat un-stopped. The monitor now cross-checks `eval_amount / qty` from the same
  `/positions` row and, above `quote_divergence_pct` (1 %), believes the worse (lower) price.

- **`GET /quotes` with no codes is free.** The bridge answers it from `RealtimeHub.latest_quotes()`
  (the StockCur tick snapshot) instead of calling `StockMst`, so a 1 s sub-cycle poll costs nothing
  on the Daishin budget. Every tick carries `ts` (epoch seconds) — reject stale ones. Still never
  decide an exit from the cache alone: `/positions` `eval_amount / qty` is the broker mark and the
  worse (lower) of the two decides.
- **Entry budget, cadence, logs, per-screener strategy**

- **The daily entry cap lives in TWO configs:** `runner.r2_max_entries_per_day` and the engine's
  `Config.daily_max_trades` (both gate entries; the engine's was 10, so raising only the runner's
  leaves it binding). With the cap far above account capacity, cash and `max_position_pct`
  (20 %/stock) become the real ceilings.
- **A large cap needs the per-code guard:** `r2_one_entry_per_code_per_day` skips codes already
  entered today (journal `codes_entered_today`). Without it a short cycle cadence re-buys the same
  candidate list every cycle until cash runs out -- the failure mode a small cap used to mask.
- **Exit levels are per-trade, not per-config.** The monitor reads `trades.stop_pct` / `tp_pct`
  from the open journal row and only falls back to the config defaults, so changing
  `take_profit_pct` (config.py or `TRADER_CORE_TAKE_PROFIT_PCT`) moves *new* entries only -- an open
  position keeps the level recorded at entry. Rewrite open rows with
  `Python312-64 tools\set_open_tp.py --tp 0.07 [--dry-run]`, then restart the loop so new plans pick
  up the new config (the running process holds the Config it started with).
- **The configured TP is a ratio, not an absolute level.** The engine sets
  `tp = volatility_stop_pct(day_range) x (tp_pct / stop_loss_pct)`, so tp=sl=0.07 means a 1:1 target
  that *widens with the trade's own day range* (range 16 % -> stop/tp 8 %, range 30 % -> 15 %, capped
  by `max_stop_pct`). A literally fixed 7 % needs `stop_range_factor: 0` + `max_stop_pct: 0.07` too;
  forcing a fixed TP under a wider stop makes the trade negative-expectancy.
- **Edit the journal on Windows, not from WSL.** WSL reads of `journal/trade_journal.sqlite3` go
  through drvfs: they raise `disk I/O error`, or silently miss rows that live in the `-wal` file
  (fresh exits looked invisible until `.sqlite3` and `-wal` were copied together). Run journal tools
  with `Python312-64`, and copy both files when inspecting from WSL.
- **Cadence is per-window** (`runner/schedule.py::cycle_interval`, `focus_windows`): 60 s inside
  09:00-09:30, 13:00-14:00, 14:50-15:30, else `interval_seconds` (300). Each cycle costs broker
  requests, so only windows that matter get the fast cadence.
- **Detached logs: read `runner.log` / `bridge.log`, not `loop.out.log`.** Shell `>>` redirection
  block-buffers stdout, so a healthy detached process leaves an empty `.out.log` for hours. Both
  entrypoints call `runner.logutil.install_file_logging`, which tees stdout/stderr into a flushed
  file (rotates to `.1` at 5 MB). Diagnose "the loop does nothing" by reading that file, not
  `loop_state.json` alone.
- **Per-screener strategy lives in `trader_core/profiles.py`** and is recorded on the journal row
  (`trades.screener`, schema v2). 종가 lists may only be bought 14:50-15:29 and are sold at the next
  open (09:00-09:10); 스윙/self-generated 랭크 lists run stop (-7 %)/take-profit (+15 %). Unknown
  screener names default to swing behaviour; the loop enforces both the window and the time exit.
- **Portfolio brakes exist because Kelly sizes one bet only:** `max_open_positions` (10) and
  `max_invested_pct` (0.40) in the engine's gate 6; the trailing stop (`trailing_stop_pct` 5 %,
  armed at `trailing_arm_pct` 3 %) stores its high-water mark in `trades.hwm_price` (schema v3) so
  it survives restarts; `rankfeed` params `max_pct` (12) refuses to chase a name already up more
  than that on the day.
- **A cold-start Kelly prior cannot be "safe-pessimistic":** `f* = p - (1-p)/b` is <= 0 for any
  `p <= 0.50` at 1:1, so 0.45 would silently stop *all* trading. 0.53 keeps a thin edge (1.5 % per
  position); `tools/review.py` replaces the priors with shrunk journal evidence once 5 trades close.
- **Clock skew breaks the whole session model** (market hours, focus windows, day rollover). The
  loop compares against an external HTTP `Date` (`trader_core/clockcheck.py`, 5 min tolerance,
  10 min cache) and blocks *new entries* when it disagrees; exits keep running, and an unreachable
  clock source only warns unless `clock_check_fail_closed` is set. On 2026-09-10 a ~-4 h skew let
  the loop trade, then run its day-end logic the moment NTP corrected the clock.

## Request budget (Daishin blocks abusive clients)

- `GetLimitRemainCount(t)`: 0 = order, 1 = quote, 2 = realtime. Call `wait_for_request_slot(t)`
  before the matching `BlockRequest`; `ret == 4` means re-request after `LimitRequestRemainTime`.
- Order/cancel use type 0; `/quotes` and **every `/rank` page** use type 1.
- Prefer realtime subscribe + cached `/quotes` over polling; never re-query a code in a loop.

## Operating sequence

```bat
bridge_start_bg.bat                 :: 32-bit bridge, logs to bridge.log (flushed)
curl http://127.0.0.1:8100/health   :: connected:true before anything else
run_trader_core.py scan --feed rank --dry-run          :: candidates through the gates
run_market_loop.py run --once --feed rank --dry-run
run_market_loop.py run --once --live --yes --feed rank :: paper account
loop_start_bg.bat / loop_stop.bat   :: unattended loop / stop
```

**The loop exits when the day is closed** (`daily.closed=True` in `loop_state.json`), so
`loop_start_bg.bat` does nothing until the next date rolls the day counters over -- start it in the
morning, not the evening. Also start it *after* the bridge reports `connected:true`, otherwise the
first cycle burns the check-failure budget.

**A connectivity halt now pauses instead of exiting** (`RunnerConfig.auto_resume_check_halt`, default
ON; `runner/loop.py::_auto_resume_halt`). When `max_consecutive_check_failures` is reached the loop
sets the HALT flag but keeps cycling, re-probing `/health` + `/balance` + feed freshness
(`_probe_ok`); when all three pass it clears the halt itself (log/notify `RESUME (auto)`). Before
2026-09-21 it exited the process, so a 17-minute link outage left the book unmanaged until a human
typed `resume` -- R4's loss halt and the kill-switch file are still hard stops on purpose.

**Settling "did we miss an exit?" after a monitoring gap** needs data the loop never stored:
`tools/gap_audit.py` (64-bit, no bridge/broker requests) pulls the day's 1-minute closes from Naver's
chart endpoint (`fchart.stock.naver.com/sise.nhn?timeframe=minute`) and compares each open position's
own exit line (journal `stop_pct` + `hwm_price`) per window you name (`--window gap=1246-1303`).
Measured 2026-09-21 (17-minute outage): the only breaches were in the pre-open window, before the
trail was even armed, so no exit was missed -- report it as the bounded claim it is, since 1-minute
closes hide an intrabar spike. Launching Windows bats from WSL can leave the WSL call
hanging: use `background=true` and verify with `tasklist`/`/health`, never by waiting on the call.

**After any Creon login/re-login, restart the bridge.** A bridge process that was already
running keeps a stale COM connection and answers `connected:false` forever, while a *fresh*
process reports `CONNECTED`. Diagnose with `run_bridge.py --no-server` (fresh) vs `/health`
(bridge): fresh CONNECTED + health false => just restart the bridge.

**A RUNNING BRIDGE BLOCKS THE CREON UPDATER (measured 2026-09-29).** The starter's file patch
("다운로드 오류 (NCFSYS, exitcode=-1)" · "다운로드 실패") is *not* a network problem even though it looks
like one: the downloads complete (141 MB `cct.dat` + 24 MB `cct.dat.zip` land in
`C:\daishin\ncfsys\spednettmp\data`), but the **32-bit bridge still attached to CybosPlus memory-maps
`C:\daishin\ncfsys\data\cct*.dat`**, so spdn cannot replace those files and exits -1 — and the login
never completes. Rule out the decoys first: check disk free space (139 GB free here), the tunnel
(`mihomo` up, `--noproxy '*'` 204), and the updater's own log (`{CREON,NCFSYS,HTS}/spednet_time.log`
— the NCFSYS one shows `total downfile Num-> 15` and the per-file byte counts). To name the lock
holder, do not guess: the Restart Manager API does it (`tools/../proxy-tun/who_locks.ps1` pattern —
`RmStartSession`/`RmRegisterResources`/`RmGetList`, or try `[System.IO.File]::Open($p,'Open','ReadWrite','None')`
to confirm the lock). Measured holder was `pid 9476 = Python` = the bridge. **Fix: stop the bridge
before any Creon login/update, and keep it stopped until the HTS is up and `spdn`/`coStarter` have
exited** — `tools/supervise_bridge.ps1` now does exactly that (it stops the bridge when Creon is down
or the updater is running, and starts it only afterwards). Never leave a bridge alive "just in case"
across a login: that is what blocks the next session's patch.

**`/health connected:true` is NOT proof of a live session (measured 2026-09-28 21:23).** Daishin
dropped the session after-hours; the HTS showed `비정상 접속 종료` and its main window (`coMain`)
closed while **CpStart/DibServer survived as orphaned components**. A bridge attached to that
orphaned COM kept answering `connected:true` (`IsConnect` is a COM-level flag) while every broker
request threw `DibRq 통신요청 실패하였습니다` -- a half-dead session that looks green. Therefore:
judge "the HTS is up" by **coMain only** (never by CpStart/DibServer), and treat `/health` as
necessary-but-not-sufficient: probe `/balance` (`ok:true`) before believing the stack is healthy.
A restart cannot revive this state -- it needs a human re-login. `tools/supervise_bridge.ps1`
implements this (coMain check, /balance probe, one restart then `RE-LOGIN REQUIRED` + 5-minute
backoff so it cannot restart-storm). Same trap applies to any external watcher: a health endpoint
that only reflects a client-side flag will hide a dead broker session.
**The elevated-session trap (measured 2026-09-22).** Creon PLUS runs elevated, and a
*medium-integrity* CybosPlus client (any python launched from WSL, `bridge_start_bg.bat` run
without UAC, `cmd.exe /c` interop) **cannot attach to that session**: it spawns a brand-new
`DibServer.exe` with no session behind it and reports `CpCybos.IsConnect == 0` forever. So
"NOT CONNECTED" from WSL is **not** evidence that the HTS is down.
- Tell them apart: `Get-Process -Id X | Select Path` returns the image path for a process at
your integrity level and is **empty/denied for the elevated Creon processes**; `taskkill /PID`
on `CpStart/DibServer/StSess` also returns `액세스가 거부되었습니다`. Both mean "try again as
admin", not "re-login".
- `C:\CREON\STARTER\ncstarter.log` (cp949) is the ground truth for the sign-on: a
`CStarterDlg::DoSignOnCybos 사인온 성공` line means the HTS **is** logged in — do not ask the
human to log in again when that line is present for the last restart.
- `CpCybos` also exposes `CreonPlusConnect`/`CybosPlusConnect`, but they take arguments
(`매개 변수의 개수가 잘못되었습니다` with none) — they are the programmatic-credential path, not
a no-arg "reconnect". Never guess credentials to use them.
- Start the bridge the way the runbook does: `bridge_run_admin.bat` (UAC) — a bridge started
by WSL interop is medium integrity and can never connect. `bridge_start_bg.bat` alone is only
correct when the whole stack is already non-elevated.
**The reverse case needs a human.** When a *fresh* one-shot also prints `NOT CONNECTED` and the
HTS's own process is missing (`tasklist /v /fo csv` shows only the components -- `CpStart.exe`,
`DibServer.exe`, `StSess*.exe`), the CybosPlus session itself is gone: restarting the bridge cannot
help (measured 2026-09-21: a fresh one-shot connected at 12:47 and the same check failed again at
12:49 while the HTS was out). Ask for the Creon PLUS login, then restart the bridge once.

**A host reboot is the first suspect when "Creon 로그인이 갑자기 안 된다" (measured 2026-09-28).** The Creon session never survives a reboot, and the starter can come back *half-dead*: `coStarter.exe` (window title `CREON Starter`) plus three `spdn.exe` (Spednet security module, one of them ESTABLISHED to Daishin 211.62.255.x:7777), but **no `coMain.exe` / `CpStart.exe` / `DibServer.exe`** — so the login/HTS window never appears and the starter sits in the 최신 파일/update check. Diagnose by listing `Get-CimInstance Win32_Process` names against a known-good day; `ncstarter.log` cannot be read while coStarter holds it (`PermissionError` = sharing violation, **not** an ACL problem, so do not "fix permissions"). Fix: kill coStarter + spdn in Task Manager, relaunch the starter **elevated**, and if it stalls again in the file-update step run `C:/CREON/STARTER/RepairGiant.exe`; old `coStarter.exe.dmp` files in `C:/CREON/STARTER/` are the fingerprint of a previous identical crash.

**A network that only carries proxied HTTP(S) breaks Creon silently (measured 2026-09-28).** On a
phone-hotspot line whose internet exists only through an HTTP proxy (`192.168.196.145:8080`, Wi-Fi DNS
= the same host), Creon's own components can reach nothing: direct TCP completes the handshake and then
**resets in the data phase**. Fingerprints, in order: `C:/CREON/{STARTER,NCFSYS,HTS}/spednet_time.log`
shows `TcpSocketInit connect success` then `파일 다운로드 실패(헤더 송수신중 connection reset) =>
Connection retry!!`, `spednettmp/spnmain.crc.zip` lands as a 1.5 KB (or 0 B) stub, then `coStarter.exe`
hangs (WER `AppHang`, then `APPCRASH` in `RPCRT4.dll`, exception `0xC0020043` = RPC_S_SERVER_UNAVAILABLE)
and the HTS/login window never appears. `Test-NetConnection <daishin-ip> -Port 7777` says **True** and
DNS/HTTP work, so "network is fine" is the wrong conclusion — verify with the data phase: a TLS
handshake (`SslStream.AuthenticateAsClient`) direct vs `curl.exe -x <proxy>` reveals that only the
proxied path carries bytes. RepairGiant does not help (and `expn.exe`/nProtect denies it).
Fix: a TUN tunnel that pushes raw TCP through the proxy's CONNECT — `C:/Users/jhshi/proxy-tun/`
(`mihomo.exe` + Tailscale's `wintun.dll` + a **generated** `config.yaml` routing everything but the
hotspot's own /24 (`172.17.112.0/20` (WSL) and loopback stay direct too), started by `run_tunnel.ps1`
under the scheduled task `trader-tunnel` (logon trigger, RunLevel *Highest*). Verified end to end:
`spnscreen.crc`/`v2dstarter.crc` patch completes in ~1.1 s, `coMain/CpStart/DibServer` come up, and the
login succeeds. Kill the tunnel and Creon dies with it — that task is now part of the market-day
prerequisites.

**The proxy address MUST be discovered, never hard-coded (measured 2026-09-30).** Restarting the hotspot
hands out a new subnet (`192.168.196.0/24` → `192.168.32.0/24`; the phone is gateway *and* DNS, proxy on
`:8080`). What that looks like when the address is baked into the config: `tunnel.log` prints
`proxy 192.168.196.145:8080 not reachable - waiting, tunnel stays off` forever, **and** a mihomo left over
from the previous day keeps its TUN policy routes, so every *direct* (non-proxy) socket on the PC dies —
`curl.exe --noproxy '*' http://www.gstatic.com/generate_204` → `000` after 5 s while the browser is fine.
The owner reads that as "프록시에 접근할 수 없다 / the proxy is down"; the proxy was healthy at its new
address the whole time. `run_tunnel.ps1` v2 now (a) resolves the proxy host at runtime from
{IPv4 default gateway, DNS servers} of every non-TUN/non-Tailscale interface × port 8080, (b) regenerates
`config.yaml` (proxy `server:` **and** the `route-exclude-address`/`DIRECT` CIDR for that /24 — a stale
CIDR makes the tunnel swallow its own upstream), (c) stops an inherited mihomo before its wait loop,
(d) re-probes the upstream every 30 s and stops mihomo after 2 consecutive misses (direct fallback beats a
black-hole), (e) takes `-Probe` to print candidates + found host without changing anything. Diagnose a
future "터널 안 붙는다" in this exact order: `-Probe` output → `tasklist /FI "IMAGENAME eq mihomo.exe"` →
`Get-NetRoute -InterfaceIndex <tun>` (0 routes = no tunnel) → `curl.exe --noproxy '*'` 204 probe.

**Check your own elevation before routing an admin step to the human**: WSL interop children are
*High* integrity when the Windows terminal hosting WSL was started as administrator
(`whoami /groups | findstr S-1-16-12288`, and `schtasks /Create /XML ... /F` from WSL then succeeds —
measured 2026-09-28). Only when that shows Medium do you need the human for UAC work.

**Do not put a PowerShell pipeline inside a batch `for /f "usebackq"` backquote block.** cmd hands the
pipe to PowerShell as a literal `^|` and the command fails *silently* (measured 2026-09-28: a
`taskkill`-by-command-line fallback never matched). Put the pipeline in a `.ps1` file and call it with
`-File` (`tools/loop_pids.ps1` is the reference).

**The stack also has Windows scheduled tasks**: `trader-bridge` (08:45) and `trader-loop` (08:50), Mon-Fri, runlevel *Limited*, actions `bridge_start_bg.bat` / `loop_start_bg.bat` — check `Get-ScheduledTask` + `Get-ScheduledTaskInfo` (LastRunTime/NextRunTime) before telling a human to start anything. They fire only once the HTS is logged in, and a *Limited* task cannot attach to an elevated Creon session; that is when `bridge_run_admin.bat` is the right start.

## Journal vs broker truth

The journal is the system's memory, and it has been wrong in three distinct ways in one day:
journaled a market exit at the echo price `0` (booked a whole position as a loss), journaled an
accepted-but-*unfilled* order as a position, and left a phantom close. Broker-side tools exist --
use them instead of reasoning from the journal:

```bat
Python312-64 tools\portfolio_check.py        :: P&L + flags journal/broker mismatches
Python311-32 tools\daily_pnl.py              :: CpTd6032: the REAL 평균매도단가 / realised P&L
Python312-64 tools\reconcile_journal.py [--apply --fix-exit CODE=PRICE]
```

Never invent a price during reconciliation: use the broker's fill price or an explicitly passed
value, otherwise report and stop.

**`trades` has no `status` / `entry_ts` column** (measured schema: `id, ts, side, code, name, qty, price, order_type, order_resp_json, entry_reason, stop_pct, tp_pct, kelly_f, exit_ts, exit_price, exit_reason, pnl, fees, screener, hwm_price, algo_json`). An open position is `exit_ts IS NULL`. While the loop is down the book sits unmanaged and *nothing* prunes it (measured 2026-09-28: 10 open rows entered 09/15-09/21, last exit 09/21 14:51, last `decisions` row 09/22 13:58) — before any session, count the open rows + read the last `decisions` rows directly; `loop_state.json` does not show the book. A market order echoes `price: 0` -- that is "unknown", and the
engine now falls back to the market reference quote (never journal 0).

**Reconcile at least 60 s after the last fill.** Broker positions settle over seconds to ~1 minute
(one code read 3 shares at 14:55:51 and 37 shares at 14:57:22 for the same order). Reconciling
inside that window sees a *flat* broker and closes a position that was actually filled -- do not
schedule reconcile on every loop cycle for that reason. A real partial fill shows up as a journal
qty > broker qty, which rule 6 (`qty_fixed`) corrects from the broker's own fill (a row that was
  first reopened needs a second pass to converge).

**The close cycle reconciles by itself now** (`RunnerConfig.close_reconcile`, default ON; OPERATIONS
8.2). After cancelling outstanding orders it compares the journal with the broker and repairs only
where the *broker* is the authority: an unfilled order closes at cost (zero PnL), a partial fill takes
the broker's qty/price, a broker-only position is adopted, a phantom close is reopened. A journal row
whose position simply vanished with no unfilled order is **reported, never closed** -- the real exit
is a broker fill price and the live quote is not one. The summary lands in `loop_state.json` ->
`daily.reconcile`: the named lists were fixed, `report_only` (= `closed` + `needs_price` +
`bogus_exits`) needs a human, who supplies the broker's mean sell price via
`tools/reconcile_journal.py --apply --fix-exit CODE=PRICE`. An unfilled order is the evidence that
separates a phantom entry from a position sold outside the system, which is why the loop feeds
`reconcile()` the **pre-cancel** `/orders` snapshot.

Candidate source without the BC-250 feed: `--feed rank` builds candidates from the broker ranking
(`CpSysDib.CpSvrNew7043`, bridge `/rank`) → filter (skip 상한가, volume/pct floors) → top-N quotes →
documented 0-100 momentum score → `FeedSnapshot` (`trader_core/rankfeed.py`).

Self-tuning: `tools/review.py` reads closed trades from the SQLite journal and writes
`strategy_params.json`; the engine reads it on every `fetch()` (filters + Kelly priors) and the loop
at startup (`screeners`, `r1_min_avg_score`). Below `--min-samples` closed trades it writes nothing.

**Never run `tools/review.py` without `--dry-run` just to look at it.** Without the flag it rewrites
`strategy_params.json`, which the *already running* loop picks up on its next fetch, so position
sizing changes mid-session (26 closed trades moved the cold-start prior 0.53 -> 0.4306 and the
per-entry budget 74,600 -> 30,900 KRW). The tuned file also breaks two unit tests that read the
repo-root file (`runner/tests/test_cli.py::test_config_file_and_interval_flags` expects
`screeners=['swing']`; `trader_core/tests/test_rankfeed.py` expects the empty cold-start file), so a
tuned file and a green suite do not coexist until those tests are made hermetic. To undo an accidental
write, restore the stub `{"generated_at": "...", "reviewed_trades": N}` (cold-start priors) and re-run
the suite.

## Verifying changes

Run the suites on the **deployment runtimes**, not just WSL: `Python311-32 -m pytest bridge/tests`
and `Python312-64 -m pytest trader_core/tests runner/tests`, plus `python3 -m unittest discover -s
bridge/tests -t .` for the Linux path. WSL system python has no pytest; the Windows ones do.

## Duplicate loop (2026-11-17, live) — two loops managed the same book all day

The scheduled task `trader-loop` (08:50, `loop_start_bg.bat`) and the supervisor task `trader-loop-sup`
(08:30, `supervise_loop.ps1`) each started a loop, so two `run_market_loop.py` processes ran the account
concurrently. Tell-tale signs: ① two `run_market_loop` entries in `Get-CimInstance Win32_Process`,
② the same filter line twice in `runner.log` (`- PRE filter swing: 20 -> 18` ×2), ③ one process's parent
already gone (orphan). Judge with **ProcessId + ParentProcessId + CreationDate together**: parent =
`supervise_loop.ps1` → keep (supervised); orphan/other parent → kill.

`loop_start_bg.bat` does have a single-instance guard (`tools/loop_pids.ps1`), but it **fails open**: if the
PowerShell call returns nothing, `for /f` falls through and the batch starts another loop. Run by hand the
script prints both pids correctly, so the likely failure is the PowerShell invocation inside the Task
Scheduler context, not the guard logic.

Applied fix: **disable the `trader-loop` task** — starting is the supervisor's job (it only starts inside the
08:30–15:35 window on weekdays). Re-enable with `schtasks /Change /TN trader-loop /ENABLE`. If you ever want the
launcher back, make the guard pidfile-based and **fail closed** (start nothing when detection fails).

## Two traps that cost real debugging time

- **Dry-run must never consume the live daily entry budget.** Dry-run fills count in
  `daily.dry_run_entries`; `entries` is live-only (otherwise a morning dry-run blocks live trading
  for the rest of the day).
- **Rule caps must apply within a batch, not just per cycle.** R2 was checked once before the plan
  loop, so a 6-candidate cycle opened 6 entries against a max-3/day cap; it now truncates to the
  remaining budget.

## Environment tricks

- **The `patch` tool mangles Windows paths in prose**: `new_string` expands `\r`, `\n`, `\t` and
  friends, so `tools\reconcile_journal.py` lands as `tools` + CR + `econcile_journal.py` and silently
  splits the line (CRLF files hide it). Use `write_file`, or a byte-level replace in Python, when the
  text contains a backslash followed by r/n/t/a/b/f/v -- this bit the OPERATIONS.md edit.
- **WSL interop can die for a few seconds**: launching any Windows exe then fails with
  `UtilAcceptVsock:273: accept4 failed 110` and rc=1. Retry after a moment instead of diagnosing; the
  already-running Windows loop/bridge are unaffected (they are not WSL processes).

- Root in WSL without a password: `/mnt/c/Windows/System32/wsl.exe -u root -- <cmd>`.
- Launching detached Windows processes from WSL (PowerShell `Start-Process`, `cmd /c start`) often
  leaves the WSL call hanging after the child is already up — use `background=true` and verify
  separately (`netstat`, `tasklist`, `/health`).
- On Windows, close a SQLite journal before deleting its temp directory, or cleanup fails.
- **Never rewrite a repo file with Windows Python text mode.** `open(p) → read → write` translates
  `\n` to `os.linesep`, so a read-modify-write turns an LF file into CRLF and the diff becomes the
  whole file (measured: config.py 300 lines, engine.py 1046). Use `newline=""` or bytes for scripted
  edits; the `patch` tool preserves the existing line endings.
- The Windows console is **cp949**: printing a character it cannot encode (em-dash `—`, middle dot)
  in a Korean tool's output kills the script mid-run. Keep printed punctuation ASCII.
- `cmd /c <bat>` and `Start-Process` from WSL can hang the WSL call while the child starts fine --
  issue them with `background=true` and verify separately, rather than waiting on the call.
