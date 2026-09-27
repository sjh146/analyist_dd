#!/usr/bin/env bash
# bridge_preopen_probe.sh — 개장 전(08:10) 브리지 상태 판정. 1회성, 읽기 전용.
#
# 왜 필요한가: 08:45 예약작업(trader-bridge, Limited/Interactive)이 띄우려는 두 번째 브리지는
#   8100 포트가 이미 점유돼 있으면 죽는다. 그때 살아 있는 브리지가 낡은 세션(connected=false)이면
#   루프는 3회 체크 실패로 자기차단 래치에 걸린다(2026-09-24 실측: 78사이클 전부 halted, 체결 0).
#   개장 전에 "그대로 둬도 되는가 / 사람이 재기동해야 하는가"를 먼저 판정한다.
#
# 판정 규칙: /health connected=true + /balance 가 숫자를 주면 → 재기동 불필요.
set -uo pipefail

PY=/mnt/c/Users/jhshi/Python312-64/python.exe
STATE=/mnt/c/Users/jhshi/analyist_dd/trader-agent/loop_state.json

probe=$("$PY" -c "
import urllib.request, json
def get(p):
    try:
        return json.loads(urllib.request.urlopen('http://127.0.0.1:8100'+p, timeout=10).read().decode())
    except Exception as e:
        return {'err': type(e).__name__}
h=get('/health'); b=get('/balance'); n=get('/positions')
bal=(b.get('balance') or {})
print('CONNECTED=%s' % h.get('connected'))
print('HEALTH=%s' % h.get('err', 'ok'))
print('EQUITY=%s CASH=%s POS=%s' % (bal.get('equity'), bal.get('cash'), bal.get('positions_count') if bal else b.get('err','?')))
print('POSITIONS_REPLY=%s' % (len(n.get('positions') or []) if not n.get('err') else n['err']))
" 2>/dev/null | tr -d '\r')

state=$(python3 - "$STATE" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print("LOOP_STATE=읽기실패(%s)" % type(e).__name__); raise SystemExit
loop = d.get("loop") or {}
daily = d.get("daily") or {}
print("LOOP halt=%s daily_closed=%s daily_date=%s closed_pnl=%s" % (
    loop.get("halt", d.get("halt")), daily.get("closed"), daily.get("date"), daily.get("closed_pnl")))
PYEOF
)

connected=$(printf '%s\n' "$probe" | sed -n 's/^CONNECTED=//p')
equity=$(printf '%s\n' "$probe" | sed -n 's/.*EQUITY=\([^ ]*\) .*/\1/p')

echo "== 08:10 개장 전 브리지 점검 =="
printf '%s\n' "$probe" | sed 's/^/  /'
echo "  $state"
echo
if [ "$connected" = "True" ] && [ -n "$equity" ] && [ "$equity" != "None" ]; then
  echo "판정: 브리지 정상 (connected=true, equity=$equity)"
  echo "  → 재기동 불필요. 08:45 예약작업이 두 번째 브리지를 띄우려다 8100 점유로 실패하는 것은 정상·무해입니다."
  echo "  → 08:50 예약작업이 매매 루프를 자동 기동합니다. 사람이 할 일은 없습니다."
else
  echo "판정: ⚠ 브리지 미연결/무응답 (CONNECTED=$connected) → 재기동 필요"
  echo "  08:35 순서: (1) HTS(Creon PLUS) 실행·로그인"
  echo "    (2) 관리자 PowerShell: Get-NetTCPConnection -LocalPort 8100 -State Listen | % { Stop-Process -Id \$_.OwningProcess -Force }"
  echo "    (3) C:\\Users\\jhshi\\analyist_dd\\trader-agent\\bridge_run_admin.bat (UAC)"
  echo "    (4) curl http://127.0.0.1:8100/health 가 connected:true 인지 확인 → 08:50 루프 자동 기동"
fi
