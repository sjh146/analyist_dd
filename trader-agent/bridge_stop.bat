@echo off
REM ---------------------------------------------------------------------------
REM creon-bridge stop - kills whatever is LISTENING on the bridge port
REM ---------------------------------------------------------------------------
setlocal
set PORT=%1
if "%PORT%"=="" set PORT=8100

set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
    echo killing PID %%p on port %PORT%
    taskkill /PID %%p /F
    set FOUND=1
)
if "%FOUND%"=="0" echo nothing listening on port %PORT%
endlocal
