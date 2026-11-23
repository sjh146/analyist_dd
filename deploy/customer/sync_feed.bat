@echo off
setlocal
cd /d "%~dp0"
set LOG=logs\sync_feed.log
if not exist logs mkdir logs
echo [%date% %time%] sync start >> "%LOG%"
python sync_feed.py --config config.json >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo [%date% %time%] sync exit %RC% >> "%LOG%"
exit /b %RC%
