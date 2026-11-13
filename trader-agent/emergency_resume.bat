@echo off
REM ---------------------------------------------------------------------------
REM 실계좌 비상 정지 해제 + 루프 재기동.
REM   kill_switch.txt 를 지우고 loop_start_bg.bat 를 부른다.
REM   (실계좌에서는 먼저 broker 연결과 한도를 확인할 것: docs/OPERATIONS.md 참조)
REM ---------------------------------------------------------------------------
setlocal
set HERE=%~dp0
if exist "%HERE%kill_switch.txt" (
    del "%HERE%kill_switch.txt"
    echo kill_switch.txt removed
) else (
    echo kill_switch.txt 없음 (이미 해제됨)
)
call "%HERE%loop_start_bg.bat"
endlocal
