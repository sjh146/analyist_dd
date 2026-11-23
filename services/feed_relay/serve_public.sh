#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-data/feed_relay}"
PORT="${PORT:-8095}"
URL_FILE="${URL_FILE:-/tmp/feed_relay_url.txt}"
cd "$(dirname "$0")/../.."

if ! curl -s --max-time 3 --noproxy '*' "http://127.0.0.1:${PORT}/health" >/dev/null; then
  nohup python3 services/feed_relay/server.py --port "$PORT" --root "$ROOT" \
    > /tmp/feed_relay.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -s --max-time 2 --noproxy '*' "http://127.0.0.1:${PORT}/health" >/dev/null && break
    sleep 0.5
  done
fi

if [ -n "${TAILSCALE_DOMAIN:-}" ]; then
  echo "Tailscale Funnel: https://${TAILSCALE_DOMAIN}"
  exec tailscale funnel --bg "$PORT"
fi

CF="${CLOUDFLARED:-/tmp/cloudflared}"
if [ ! -x "$CF" ]; then
  echo "cloudflared 없음. 파일럿: curl -sL -o /tmp/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x /tmp/cloudflared" >&2
  exit 2
fi

nohup "$CF" tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate > /tmp/cf_tunnel.log 2>&1 &
for _ in $(seq 1 40); do
  URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/cf_tunnel.log | head -1 || true)"
  [ -n "$URL" ] && break
  sleep 1
done
if [ -z "${URL:-}" ]; then
  echo "터널 URL 생성 실패 — /tmp/cf_tunnel.log 확인" >&2
  exit 3
fi
echo "$URL" > "$URL_FILE"
echo "공개 URL: ${URL}"
echo "고객 config 의 base_url 을 위 값으로 설정한다. (빠른 터널은 재시작마다 URL 이 바뀐다)"
