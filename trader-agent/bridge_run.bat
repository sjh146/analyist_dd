@echo off
REM Creon bridge (32-bit Python ONLY). Paths resolve from this file's folder,
REM so a fresh clone runs unchanged. Override the interpreter with TRADER_PY32.
setlocal
cd /d "%~dp0"
if defined TRADER_PY32 (set "PY=%TRADER_PY32%") else (set "PY=%USERPROFILE%\Python311-32\python.exe")
if not exist "%PY%" (
    echo [error] 32-bit python not found: %PY%
    echo         set TRADER_PY32 to your Python 3.x 32-bit python.exe
    endlocal
    exit /b 1
)
"%PY%" run_bridge.py --port 8100
endlocal
