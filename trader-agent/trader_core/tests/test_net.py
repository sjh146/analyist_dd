"""Regression tests for :mod:`trader_core.net` (proxy poisoning of loopback).

Measured 2026-09-28 on the trading host: with the Windows system proxy enabled
(``ProxyEnable=1`` -> ``http://192.168.196.145:8080``, bypass list without
``<local>``) a plain ``urllib.request.urlopen("http://127.0.0.1:8100/health")``
returned ``HTTP 502`` while ``curl`` to the same URL returned the bridge's JSON.
``no_proxy`` does not help on Windows (``proxy_bypass`` reads the registry).
These tests pin the two things that must stay true:

1. loopback/private/tailnet hosts are classified as local;
2. local URLs are fetched through the no-proxy opener, and only those.
"""

from __future__ import annotations

import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from trader_core import net


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "::1", "0.0.0.0", "10.1.2.3", "192.168.1.9",
     "172.16.5.4", "169.254.10.10", "100.93.220.52"],
)
def test_local_hosts_detected(host):
    assert net.is_local_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "1.1.1.1", ""])
def test_public_hosts_not_local(host):
    assert net.is_local_host(host) is False


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output clean
        return


@pytest.fixture()
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:{0}".format(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def test_urlopen_bypasses_proxy_for_localhost(monkeypatch, local_server):
    """A poisoned ``http_proxy`` must not break a loopback fetch."""
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")   # dead port
    monkeypatch.setenv("no_proxy", "")
    with net.urlopen(local_server + "/health", timeout=5) as resp:
        assert resp.status == 200
        assert b'"ok": true' in resp.read()


def test_urlopen_uses_stdlib_for_public_hosts(monkeypatch):
    """Non-local hosts keep the (proxy-aware) stdlib path untouched."""
    seen = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        seen["timeout"] = timeout
        return "sentinel"

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = net.urlopen("https://example.com/hook", timeout=7.5)
    assert out == "sentinel"
    assert seen == {"url": "https://example.com/hook", "timeout": 7.5}


def test_urlopen_local_never_calls_stdlib(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("stdlib urlopen must not be used for local hosts")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(Exception):
        # nothing listens on port 9 -> transport error, but not the assertion
        net.urlopen("http://127.0.0.1:9/health", timeout=1)
