@echo off
REM ---------------------------------------------------------------------------
REM Autonomous market-hours loop (64-bit Python) - detached, with logs.
REM Candidate source: analyist_dd feed (TRADER_FEED_URL) or Creon ranking (TRADER_FEED=rank).
REM Stop with loop_stop.bat, or drop kill_switch.txt to liquidate + halt.
REM Logs: runner.log (flushed in-process) + loop.out.log / loop.err.log (shell)
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
if defined TRADER_PY64 (set "PY=%TRADER_PY64%") else (set "PY=%USERPROFILE%\Python312-64\python.exe")
if not defined TRADER_FEED (set "TRADER_FEED=http://127.0.0.1:8090/screener_latest.json")
if not defined TRADER_CONFIG_EXTRA (set "TRADER_CONFIG_EXTRA=")

REM ---------------------------------------------------------------------------
REM NB: cmd ends a `for ... do (` block at the FIRST ')' even when it is balanced
REM inside an echo -- '... (pid 123)' followed by more text makes cmd run the rest
REM ('- skip start') as a new command and the whole script dies with rc=255
REM ('- was unexpected at this time', measured 2026-09-28).  No parens in the body.
REM Single-instance guard: the 08:50 scheduled task may fire while a loop is
REM already running (manual start, or a 60s focus window).  Two loops would
REM double-manage the same book (duplicate exits/entries), so refuse.
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\loop_pids.ps1"`) do (
    echo [%DATE% %TIME%] market loop already running pid=%%p - skip start >> loop.out.log
    endlocal
    exit /b 0
)

echo [%DATE% %TIME%] starting market loop (--loop --live --feed %TRADER_FEED%) >> loop.out.log
start "trader-loop" /min "%PY%" -u run_market_loop.py run --loop --live --yes --feed %TRADER_FEED% %TRADER_CONFIG_EXTRA% >> loop.out.log 2>> loop.err.log
endlocal
