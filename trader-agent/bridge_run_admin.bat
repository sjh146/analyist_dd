@echo off
REM Elevated bridge start (the bridge must attach to the elevated Creon session).
REM Paths resolve from this file's folder; override the interpreter with TRADER_PY32.
setlocal
cd /d "%~dp0"
if defined TRADER_PY32 (set "PY=%TRADER_PY32%") else (set "PY=%USERPROFILE%\Python311-32\python.exe")
powershell -NoProfile -Command "Start-Process -FilePath '%PY%' -ArgumentList '%~dp0run_bridge.py','--port','8100' -WorkingDirectory '%~dp0.' -Verb RunAs"
endlocal
