@echo off
REM ---------------------------------------------------------------------------
REM 실계좌 비상 정지 (2026-09-28 실계좌 전환과 함께 도입).
REM   1) kill_switch.txt 생성 -> 루프의 매매 게이트가 닫힌다(신규 진입·매매 중지).
REM   2) 루프 프로세스 종료(bridge 는 남긴다 - 잔고 조회는 계속 가능).
REM 주의: 보유 포지션은 그대로 남는다. 전량 청산을 원하면 HTS 에서 직접 매도하거나,
REM       루프를 켠 채로 두고 자동 R4(일일 -3%)를 기다리지 말고 수동으로 청산할 것.
REM 되돌리기: emergency_resume.bat
REM ---------------------------------------------------------------------------
setlocal
set HERE=%~dp0
echo emergency stop %DATE% %TIME% by operator> "%HERE%kill_switch.txt"
echo --- kill_switch.txt created ---
type "%HERE%kill_switch.txt"
call "%HERE%loop_stop.bat"
echo.
echo loop stopped. positions are UNTOUCHED (청산하지 않았다).
endlocal
