@echo off
REM ---------------------------------------------------------------------------
REM Stop the autonomous market-hours loop started by loop_start_bg.bat.
REM Positions are left untouched; its presence in loop_state.json is kept.
REM ---------------------------------------------------------------------------
setlocal
set FOUND=0

REM 1) the console window created by: start "trader-loop" /min python ...
taskkill /FI "WINDOWTITLE eq trader-loop*" /F >nul 2>&1 && set FOUND=1

REM 2) fallback: PIDs of python.exe running run_market_loop, via the shared
REM    helper (an inline powershell pipeline inside a backquote block does not
REM    survive cmd escaping - measured 2026-09-28).
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\loop_pids.ps1"`) do (
    echo killing PID %%p
    taskkill /PID %%p /F
    set FOUND=1
)

if "%FOUND%"=="0" echo no running trader-loop found
endlocal
