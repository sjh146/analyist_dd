#!/bin/bash
# analyist_dd 일봉 수집 디스패처
#   기본 = KRX OpenAPI (날짜당 2콜, D-1/D-2 발행 지연은 스크립트가 처리)
#   폴백 = KRX 키가 없거나 KRX가 차단(exit 3)일 때 KIS 일봉 (종목당 1콜 ≈ 2,770콜 → 약 2.5h)
# 변경 이력: 2026-09-23 — "KIS 키 있으면 KIS" 분기 때문에 키를 넣는 순간 매일 밤 2.5시간짜리
#   KIS 수집으로 바뀌고, 20:00 파이프라인이 부분 적재된 데이터를 읽게 되던 문제를 수정.
#   되돌리려면 /home/jhshi/cron/daily_bars.sh.bak-20260923 을 복사해 덮으면 된다.
set -a; . /home/dduckbeagy/analyist_dd/.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="${POSTGRES_HOST_PORT:-5434}"
cd /home/dduckbeagy/analyist_dd || exit 1

if [ -n "${KRX_API_KEY:-}" ]; then
    echo "[$(date '+%F %T')] 경로: KRX OpenAPI"
    /usr/bin/python3 scripts/krx_daily.py --days 7
    rc=$?
    if [ "$rc" -eq 0 ]; then
        exit 0
    fi
    echo "[$(date '+%F %T')] KRX 실패(exit=$rc) — KIS 폴백 시도"
fi

if [ -n "${KIS_APP_KEY:-}" ] && [ -n "${KIS_APP_SECRET:-}" ]; then
    echo "[$(date '+%F %T')] 경로: KIS 일봉 (토큰 점검 후)"
    cd services/kis-collector || exit 1
    # 키가 '설정만' 되어 있고 인증이 실패하는 상태면 2,770콜을 전부 실패로 날린다.
    # 토큰 1콜로 먼저 확인하고, 실패 시 원인(EGW####)을 로그에 남긴다.
    if /usr/bin/python3 -m kis_app.main --probe-token; then
        exec /usr/bin/python3 -m kis_app.main --job daily --date "$(date +%Y%m%d)"
    fi
    echo "[$(date '+%F %T')] KIS 토큰 발급 실패 — 수집 중단 (앱키/시크릿·실전/모의 도메인 확인)"
    exit 3
fi

echo "[$(date '+%F %T')] KRX·KIS 모두 사용 불가 (키 없음)"
exit 2
