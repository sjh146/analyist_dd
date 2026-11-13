"""Proxy-safe HTTP for loopback / private-network hosts.

Why this exists (measured 2026-09-28, before the open): the Windows host has a
**system proxy enabled** (registry ``ProxyEnable=1`` ->
``http://192.168.196.145:8080``) whose bypass list does **not** contain
``<local>``.  ``urllib.request.urlopen`` therefore routes even
``http://127.0.0.1:8100/health`` through the proxy, which answers
``502 Bad Gateway`` for loopback:

* ``bridge_client`` saw ``HTTP 502`` instead of the bridge's JSON, so every
  bridge call (health, balance, quotes, orders) failed while the bridge itself
  was answering ``200`` to ``curl``;
* ``ScreenerFeedClient`` saw ``502`` instead of the feed, so no candidates.

``urllib.request.proxy_bypass`` on Windows reads **only** the WinINET registry
list (it ignores the ``no_proxy`` environment variable), so the reliable fix is
an opener built with an empty :class:`urllib.request.ProxyHandler` for hosts
that are by definition reached directly: loopback, RFC1918 private, link-local
and the Tailscale CGNAT range ``100.64.0.0/10``.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.request
from typing import Any

__all__ = ["is_local_host", "urlopen"]

# Empty mapping => never use a proxy for this opener.
_OPENER_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_LOCAL_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }
)
_TAILNET = ipaddress.ip_network("100.64.0.0/10")  # Tailscale CGNAT


def is_local_host(host: str) -> bool:
    """Return True for hosts that must never go through a proxy.

    Loopback names/IPs, RFC1918 + link-local addresses and the Tailscale
    ``100.64.0.0/10`` range (which :mod:`ipaddress` does not classify as
    private) count as local.  Unknown hostnames fall through to the default
    (proxy-aware) behaviour.
    """
    name = (host or "").strip().lower()
    if not name:
        return False
    if name in _LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        return False
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True
    return ip.version == 4 and ip in _TAILNET


def urlopen(url_or_request: Any, timeout: float = 10.0):
    """Like :func:`urllib.request.urlopen`, but never proxied for local hosts.

    Accepts either a URL string or a :class:`urllib.request.Request`, exactly
    like the stdlib function, so callers keep their existing error handling
    (``HTTPError`` / ``URLError`` / ``OSError``).
    """
    if isinstance(url_or_request, urllib.request.Request):
        url = url_or_request.full_url
    else:
        url = str(url_or_request)
    host = urllib.parse.urlsplit(url).hostname or ""
    if is_local_host(host):
        return _OPENER_NO_PROXY.open(url_or_request, timeout=timeout)
    return urllib.request.urlopen(url_or_request, timeout=timeout)
