#!/bin/sh
# 시장·수집 경로 watchdog — 정상이면 침묵, 이상 시에만 Discord 로 한 줄씩
exec python3 /home/jhshi/.hermes/scripts/market_watchdog.py
