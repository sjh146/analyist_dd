@echo off
REM ---------------------------------------------------------------------------
REM creon-bridge detached start (32-bit Python 3.11 ONLY - CybosPlus COM)
REM Logs: bridge.log (flushed in-process) + bridge.out.log / bridge.err.log (shell)
REM Check: bridge_health.bat
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
if defined TRADER_PY32 (set "PY=%TRADER_PY32%") else (set "PY=%USERPROFILE%\Python311-32\python.exe")
set PORT=%1
if "%PORT%"=="" set PORT=8100

REM ---------------------------------------------------------------------------
REM NB: cmd ends a `for ... do (` block at the FIRST ')' even when it is balanced
REM inside an echo -- '... (pid 123)' followed by more text makes cmd run the rest
REM ('- skip start') as a new command and the whole script dies with rc=255
REM ('- was unexpected at this time', measured 2026-09-28).  No parens in the body.
REM Single-instance guard: the 08:45 scheduled task may fire while a bridge is
REM already running (e.g. one started by bridge_run_admin.bat).  A second bridge
REM would fail to bind anyway - exit quietly instead of logging a scary error.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
    echo [%DATE% %TIME%] port %PORT% already listening pid=%%p - bridge already running, skip >> bridge.out.log
    endlocal
    exit /b 0
)

echo [%DATE% %TIME%] starting creon-bridge on port %PORT% >> bridge.out.log
start "creon-bridge" /min "%PY%" -u run_bridge.py --port %PORT% >> bridge.out.log 2>> bridge.err.log
endlocal
