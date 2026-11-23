#!/usr/bin/env python3
"""고객 PC용 피드/업데이트 릴레이 서버 (표준 라이브러리만 사용).

역할: 중앙 서버가 만든 오늘의 추론 결과(feed), 코드 번들(pkg), 서명(manifest)을
고객 PC가 HTTPS 로 pull 해갈 수 있게 노출한다. 고객 PC 는 아웃바운드만 쓴다.

엔드포인트:
  GET  /health
  GET  /v1/feed?customer_id=..      Authorization: Bearer <token>
  GET  /v1/manifest?channel=stable
  GET  /v1/pkg/<version>            (bundle zip)
  POST /v1/ack                      {"customer_id","feed_generated_at","version"}
  POST /v1/telemetry                {"customer_id","version","payload":{...}}

저장 구조 (기본 data/feed_relay):
  feed/latest.json                  오늘 발행 피드(서버가 생성)
  releases/<version>/manifest.json  파일별 sha256 + 서명
  releases/<version>/bundle.zip
  channels.json                     {"stable": "1.0.3", "beta": "1.0.4"}
  customers.json                    {"customers":[{id, token_sha256, channel, limits}]}
  ack/<YYYYMMDD>.jsonl
  telemetry/<customer_id>/<YYYYMMDD>.json

실행:
  python3 services/feed_relay/server.py --port 8095 --root data/feed_relay
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_BODY = 2 * 1024 * 1024


def now_kst() -> datetime:
    from datetime import timedelta, timezone

    return datetime.now(timezone(timedelta(hours=9)))


class Relay:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.customers = {}
        self.channels = {}
        self._mtimes = (0.0, 0.0)
        self.reload()

    def _mtime(self, name: str) -> float:
        try:
            return os.path.getmtime(os.path.join(self.root, name))
        except OSError:
            return 0.0

    def maybe_reload(self) -> None:
        stamp = (self._mtime("customers.json"), self._mtime("channels.json"))
        if stamp != self._mtimes:
            self.reload()

    def reload(self) -> None:
        self._mtimes = (self._mtime("customers.json"), self._mtime("channels.json"))
        self.customers = self._load_json("customers.json", {"customers": []})
        self.channels = self._load_json("channels.json", {})

    def _load_json(self, name: str, default):
        path = os.path.join(self.root, name)
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def auth(self, customer_id: str, token: str) -> dict | None:
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for row in self.customers.get("customers") or []:
            if str(row.get("id")) != str(customer_id):
                continue
            if str(row.get("token_sha256", "")).lower() == digest:
                return row
        return None

    def feed_path(self) -> str:
        return os.path.join(self.root, "feed", "latest.json")

    def read_feed(self) -> dict | None:
        try:
            with open(self.feed_path(), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def feed_for(self, customer: dict) -> dict | None:
        feed = self.read_feed()
        if feed is None:
            return None
        payload = dict(feed)
        payload["customer_id"] = customer.get("id")
        payload["channel"] = customer.get("channel") or "stable"
        payload["limits"] = customer.get("limits") or {}
        return payload

    def manifest_for(self, channel: str) -> dict | None:
        version = str(self.channels.get(channel) or "")
        if not version:
            return None
        path = os.path.join(self.root, "releases", version, "manifest.json")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def pkg_path(self, version: str) -> str | None:
        if not version or "/" in version or ".." in version:
            return None
        path = os.path.join(self.root, "releases", version, "bundle.zip")
        return path if os.path.isfile(path) else None

    def append_ack(self, payload: dict) -> None:
        os.makedirs(os.path.join(self.root, "ack"), exist_ok=True)
        path = os.path.join(self.root, "ack", now_kst().strftime("%Y%m%d") + ".jsonl")
        row = dict(payload)
        row["received_at"] = now_kst().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def save_telemetry(self, payload: dict) -> str:
        cid = str(payload.get("customer_id") or "unknown")
        cid = "".join(ch for ch in cid if ch.isalnum() or ch in "-_")[:40] or "unknown"
        day = now_kst().strftime("%Y%m%d")
        folder = os.path.join(self.root, "telemetry", cid)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, day + ".json")
        body = dict(payload)
        body["received_at"] = now_kst().isoformat(timespec="seconds")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=2)
        return path


class Handler(BaseHTTPRequestHandler):
    server_version = "feed-relay/1.0"
    relay: Relay = None

    def log_message(self, fmt, *args):
        sys.stderr.write("[relay] %s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _token(self) -> str:
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def do_GET(self):
        self.relay.maybe_reload()
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            return self._send(200, {"ok": True, "ts": now_kst().isoformat(timespec="seconds")})
        if path == "/v1/feed":
            cid = (parse_qs(parsed.query).get("customer_id") or [""])[0]
            customer = self.relay.auth(cid, self._token())
            if customer is None:
                return self._send(401, {"ok": False, "error": "unauthorized"})
            payload = self.relay.feed_for(customer)
            if payload is None:
                return self._send(503, {"ok": False, "error": "feed_not_published"})
            return self._send(200, payload)
        if path == "/v1/manifest":
            channel = (parse_qs(parsed.query).get("channel") or ["stable"])[0]
            manifest = self.relay.manifest_for(channel)
            if manifest is None:
                return self._send(404, {"ok": False, "error": "no_manifest"})
            return self._send(200, manifest)
        if path.startswith("/v1/pkg/"):
            version = path[len("/v1/pkg/"):].strip()
            pkg = self.relay.pkg_path(version)
            if pkg is None:
                return self._send(404, {"ok": False, "error": "no_pkg"})
            with open(pkg, "rb") as fh:
                return self._send(200, fh.read(), ctype="application/zip")
        return self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        self.relay.maybe_reload()
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._send(413, {"ok": False, "error": "bad_length"})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"ok": False, "error": "bad_json"})
        if parsed.path == "/v1/ack":
            customer = self.relay.auth(str(payload.get("customer_id") or ""), self._token())
            if customer is None:
                return self._send(401, {"ok": False, "error": "unauthorized"})
            self.relay.append_ack(payload)
            return self._send(200, {"ok": True})
        if parsed.path == "/v1/telemetry":
            customer = self.relay.auth(str(payload.get("customer_id") or ""), self._token())
            if customer is None:
                return self._send(401, {"ok": False, "error": "unauthorized"})
            path = self.relay.save_telemetry(payload)
            return self._send(200, {"ok": True, "saved": os.path.basename(path)})
        return self._send(404, {"ok": False, "error": "not_found"})


def build_server(root: str, host: str, port: int, cert: str = "", key: str = "") -> ThreadingHTTPServer:
    relay = Relay(root)
    handler = type("BoundHandler", (Handler,), {"relay": relay})
    httpd = ThreadingHTTPServer((host, port), handler)
    if cert and key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="feed/update relay")
    ap.add_argument("--root", default=os.path.join("data", "feed_relay"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--cert", default="")
    ap.add_argument("--key", default="")
    args = ap.parse_args(argv)
    os.makedirs(args.root, exist_ok=True)
    httpd = build_server(args.root, args.host, args.port, args.cert, args.key)
    print("[relay] listening on {0}:{1} root={2}".format(args.host, args.port, os.path.abspath(args.root)))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[relay] 중단 요청")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
