#!/bin/bash
# swing 경로 표본 축적 (헌장 §6 자기개선 루프) — 매일 1회 프로브를 돌려 결과를 덮어쓴다.
#
# 왜: 2026-10-01 close 경로는 3개월 1,736행으로 '게이트가 옳다(기대값 음수)'가 확정됐지만,
# swing 경로는 ml_predictions 커버리지가 8거래일뿐이라 판정 불가였다(미청산 多 + n=20).
# 표본은 하루씩만 늘어나므로 자동으로 쌓아야 2주 뒤 판정이 가능하다.
# 산출물: data/reports/swing_path_probe.json (+ _detail.csv). 판정 문턱은
# 청산 완료 50건 이상 + 짝 설계일 때 리뷰보드가 판단한다(그 전에는 '표본 축적'으로만 기록).
set -a; . /home/dduckbeagy/analyist_dd/.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="${POSTGRES_HOST_PORT:-5434}"
export PROJ_DIR=/home/dduckbeagy/analyist_dd
cd /home/dduckbeagy/analyist_dd || exit 1
echo "[$(date '+%F %T')] swing 경로 프로브 시작"
exec /usr/bin/python3 scripts/_swing_path_probe.py --topn 3,5,10 --hold-days 10 \
     --out data/reports/swing_path_probe.json
