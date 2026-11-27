#!/bin/bash
# analyist_dd 분봉 수집 (KIS) — 우선순위 유니버스만, 야간 마감 시각 포함.
#
# 왜 전 종목이 아닌가 (2026-09-23 실측): KIS 당일분봉 API는 fid_cnt를 줘도 페이지당 30행만 준다
#   → 하루 391분 = 14페이지, 콜당 약 3.5초(net_guard 3.0s 포함) = 종목당 약 46초.
#   473종목이면 약 6시간이라 07:35 분봉 가드와 겹친다 → **03:30 마감**을 두고,
#   패널 200종목을 먼저 채운 뒤 남는 시간에 나머지를 채운다(잘린 종목 수는 로그에 남는다).
#
# 되돌리기: /home/jhshi/cron/kis_minute.sh.bak-20261127
set -a; . /home/jhshi/analyist_dd/.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
export KIS_MINUTE_MAX_PAGES="${KIS_MINUTE_MAX_PAGES:-14}"

REPO=/home/jhshi/analyist_dd
cd "$REPO" || exit 1

PRIO="$REPO/data/panel_priority_codes.json"
/usr/bin/python3 - "$PRIO" <<'PY' 2>/dev/null || rm -f "$PRIO"
import json, sys
import numpy as np
z = np.load("/home/jhshi/analyist_dd/data/panels/panel_prod200_v8.npz", allow_pickle=True)
json.dump(sorted({str(c) for c in z["codes"]}), open(sys.argv[1], "w"))
PY

cd services/kis-collector || exit 1
exec /usr/bin/python3 -m kis_app.main --job minute \
    --date "$(date +%Y%m%d)" \
    --universe-file "$REPO/data/minute_universe.json" \
    --priority-file "$PRIO" \
    --deadline "${KIS_MINUTE_DEADLINE:-03:30}"
