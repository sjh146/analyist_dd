"""Notification stub for the market-hours loop.

Everything is logged locally no matter what; when a webhook URL is configured
(Discord) a ``{"content": text}`` POST is also attempted with a short timeout.
Webhook failures are swallowed to a log line so a dead webhook can never crash
the trading loop.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable, Dict, Optional

_TIMEOUT_SECONDS = 3.0


def build_payload(text: str) -> Dict[str, str]:
    """Return the webhook body for *text* (pure, for tests)."""
    return {"content": text}


class Notifier:
    """Logs every message and optionally posts it to a Discord webhook."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Construct a notifier for *webhook_url* (empty = log-only).

        ``log`` is an optional sink for the always-on local line (defaults to
        ``print``); tests pass a capturing callable.
        """
        self.webhook_url = (webhook_url or "").strip()
        self.log = log if log is not None else _default_log

    def send(self, text: str) -> bool:
        """Log *text* always; POST to the webhook when configured.

        Returns True when the webhook POST was attempted successfully (or when
        no URL is set after logging).  Never raises: transport errors are
        caught and reported through the log.
        """
        self.log("NOTIFY {0}".format(text))
        if not self.webhook_url:
            return True
        try:
            self._post(self.webhook_url, build_payload(text))
            return True
        except Exception as exc:  # noqa: BLE001 - webhook must never crash us
            self.log("NOTIFY webhook failed: {0}".format(exc))
            return False

    def _post(self, url: str, payload: Dict[str, Any]) -> None:
        """POST *payload* as JSON to *url* (urllib, short timeout)."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310 - operator-supplied https webhook
            resp.read()


def _default_log(line: str) -> None:  # pragma: no cover - trivial
    print(line, flush=True)
