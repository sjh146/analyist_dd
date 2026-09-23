#!/usr/bin/env python3
"""피드 스냅샷 HTTP 서버 (trader-agent 폴링용).

- GET /screener_latest.json : 발행된 스냅샷 (no-cache 헤더)
- GET /health               : 파일 존재/나이/generated_at 요약 (모니터링용)

인증은 Tailscale 에 위임한다(FEED_CONTRACT §1). 바인딩 기본 0.0.0.0:8090 —
WSL 런타임은 tailnet 노드(100.93.220.52)로 도달 가능하고, Windows 노드는
http://100.93.220.52:8090/screener_latest.json 을 폴링한다.

사용: python3 scripts/feed_server.py [--host 0.0.0.0] [--port 8090] [--dir data/feed]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_NAME = "screener_latest.json"
_snapshot = {"dir": os.path.join(PROJ, "data", "feed"), "name": DEFAULT_NAME}


class FeedHandler(BaseHTTPRequestHandler):
    server_version = "analyist-dd-feed/1.0"

    def _send(self, code, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._health()
            return
        if path in ("/", "/" + _snapshot["name"]):
            self._snapshot_file(path == "/")
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def _snapshot_file(self, is_root: bool):
        path = os.path.join(_snapshot["dir"], _snapshot["name"])
        if not os.path.exists(path):
            self._send(503, json.dumps({"error": "feed not published yet"}).encode(),
                       "application/json")
            return
        with open(path, "rb") as f:
            body = f.read()
        mtime = os.path.getmtime(path)
        age = round(datetime.now(timezone.utc).timestamp() - mtime, 1)
        self._send(200, body, "application/json",
                   {"X-Feed-Generated-At": str(int(mtime)), "X-Feed-Age-Seconds": str(age),
                    "X-Feed-Path": "/" + _snapshot["name"]})
        if is_root:
            return

    def _health(self):
        path = os.path.join(_snapshot["dir"], _snapshot["name"])
        out: dict = {"service": "analyist-dd-feed", "file": path}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            items = {k: len(v.get("items") or []) for k, v in (data.get("candidates") or {}).items()}
            out.update({
                "exists": True,
                "generated_at": data.get("generated_at"),
                "age_seconds": round(datetime.now(timezone.utc).timestamp() - os.path.getmtime(path), 1),
                "items": items,
            })
        else:
            out["exists"] = False
        self._send(200, json.dumps(out, ensure_ascii=False).encode(), "application/json")

    def log_message(self, format, *args):   # 폴링 로그로 디스크를 채우지 않는다
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="analyist_dd 피드 서버")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--dir", default=os.path.join(PROJ, "data", "feed"))
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    _snapshot["dir"] = os.path.abspath(args.dir)
    _snapshot["name"] = args.name
    srv = ThreadingHTTPServer((args.host, args.port), FeedHandler)
    if not args.quiet:
        print(f"[feed] http://{args.host}:{args.port}/{args.name} "
              f"← {_snapshot['dir']}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
