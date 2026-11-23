#!/bin/bash
# KRX OpenAPI 서비스 프로브 — 경로별 200/401/404 를 한 번에 확인한다.
# 사용: KRX_API_KEY=<키> bash krx_service_probe.sh [YYYYMMDD]
# 키는 인자로 받지 않는다(프로세스 목록 노출 방지) — .env 를 source 해 환경변수로 전달할 것.
set -u
KEY="${KRX_API_KEY:-}"
BASE="${KRX_BASE_URL:-https://data-dbg.krx.co.kr/svc/apis}"
D="${1:-$(date -d '3 days ago' +%Y%m%d)}"
PATHS="sto/stk_bydd_trd sto/ksq_bydd_trd sto/knx_bydd_trd sto/stk_isu_base_info sto/ksq_isu_base_info idx/kospi_dd_trd idx/kosdaq_dd_trd idx/krx_dd_trd etp/etf_bydd_trd"

if [ -z "$KEY" ]; then echo "KRX_API_KEY 미설정 (source .env)" >&2; exit 2; fi
for p in $PATHS; do
  code=$(curl -s -o /tmp/.krxprobe.json -w '%{http_code}' "$BASE/$p?basDd=$D" -H "AUTH_KEY: $KEY" -H 'Accept: application/json')
  head=$(head -c 70 /tmp/.krxprobe.json | tr -d '\r\n')
  printf '%-24s %s %s\n' "$p" "$code" "$head"
  sleep 1
 done
rm -f /tmp/.krxprobe.json
