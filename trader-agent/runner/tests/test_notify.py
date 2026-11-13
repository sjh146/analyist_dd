"""Notifier payload + webhook POST behaviour tests (no real HTTP)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from runner import notify


class _FakeResponse:
    """Minimal urlopen() result that works as a context manager."""

    def __init__(self, body: bytes = b'{"ok": true}') -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class NotifierTest(unittest.TestCase):
    def _capture(self) -> list:
        lines: list = []
        return lines

    def test_build_payload_shape(self) -> None:
        self.assertEqual(notify.build_payload("hello"), {"content": "hello"})
        self.assertEqual(notify.build_payload(""), {"content": ""})

    def test_no_url_logs_and_never_http(self) -> None:
        lines: list = []
        notifier = notify.Notifier(webhook_url=None, log=lines.append)
        with mock.patch.object(notify.urllib.request, "urlopen") as urlopen:
            result = notifier.send("something happened")
        self.assertTrue(result)
        self.assertTrue(any("something happened" in line for line in lines))
        urlopen.assert_not_called()

    def test_url_set_posts_payload(self) -> None:
        lines: list = []
        captured: dict = {}
        notifier = notify.Notifier(webhook_url="https://discord.example/hook",
                                   log=lines.append)

        def fake_urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["timeout"] = timeout
            return _FakeResponse()

        with mock.patch.object(notify.urllib.request, "urlopen",
                               side_effect=fake_urlopen) as urlopen:
            result = notifier.send("buy filled")
        self.assertTrue(result)
        self.assertEqual(captured["body"], {"content": "buy filled"})
        self.assertEqual(captured["url"], "https://discord.example/hook")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["timeout"], notify._TIMEOUT_SECONDS)
        urlopen.assert_called_once()

    def test_webhook_error_is_swallowed(self) -> None:
        lines: list = []
        notifier = notify.Notifier(webhook_url="https://discord.example/hook",
                                   log=lines.append)
        with mock.patch.object(
            notify.urllib.request, "urlopen",
            side_effect=OSError("connection refused"),
        ):
            result = notifier.send("will not crash")
        self.assertFalse(result)
        self.assertTrue(any("webhook failed" in line for line in lines))

    def test_default_log_is_not_none(self) -> None:
        notifier = notify.Notifier(webhook_url=None)
        self.assertIsNotNone(notifier.log)


if __name__ == "__main__":
    unittest.main()
