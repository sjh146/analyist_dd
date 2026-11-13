@echo off
chcp 65001 > nul
REM Registers the four trading tasks for THIS machine (paths/user resolved at run time).
REM Run in an ADMIN console: the supervisors use RunLevel Highest, because a
REM medium-integrity bridge cannot attach to the elevated Creon PLUS session.
REM   register_tasks.bat -DryRun     :: 무엇을 등록할지 출력만
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\register_tasks.ps1" %*
echo --- registered ---
schtasks /Query /TN "trader-bridge"
schtasks /Query /TN "trader-loop"
schtasks /Query /TN "trader-bridge-sup"
schtasks /Query /TN "trader-loop-sup"
