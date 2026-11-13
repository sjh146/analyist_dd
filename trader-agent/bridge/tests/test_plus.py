"""Unit tests for ``bridge.plus``: the serialised STA worker and its idle COM pump.

Regression coverage for a live-only bug found 2026-09-10 on Creon PLUS
(모의투자): a realtime subscription created *after* any BlockRequest returned
``ok`` but never received a single event, because the CybosPlus message window
belonged to the blocking worker's apartment and that worker never pumped its
message queue.  Subscribing as the process's very first Cybos call worked,
which is why the earlier tests passed.

Counting note: other test modules leave their own daemon workers running, and
every worker resolves ``pythoncom`` from ``sys.modules`` on each call, so the
fake counts are keyed by the calling thread.  Assertions therefore only ever
look at the worker thread a test created itself.
"""

from __future__ import annotations

import sys
import threading
import time
import types
import unittest

from bridge.plus import _MAX_IDLE_PUMP, CybosPlus
from bridge.tests import support


class _FakePythoncom(types.ModuleType):
    """Minimal ``pythoncom`` double counting CoInitialize + pump activity."""

    def __init__(self, pending: int = 0, always: bool = False) -> None:
        super().__init__("pythoncom")
        self.pending = pending
        self.always = always
        self.pump_calls = 0
        self.by_thread = {}
        self.co_init = 0
        self.co_uninit = 0

    def CoInitialize(self) -> None:
        self.co_init += 1

    def CoUninitialize(self) -> None:
        self.co_uninit += 1

    def calls_on(self, thread) -> int:
        return self.by_thread.get(thread, 0)

    def PumpWaitingMessages(self) -> bool:
        self.pump_calls += 1
        current = threading.current_thread()
        self.by_thread[current] = self.by_thread.get(current, 0) + 1
        if self.always:
            return True
        if self.pending > 0:
            self.pending -= 1
            return True
        return False


class _FakePythoncomContext:
    def __init__(self, fake) -> None:
        self.fake = fake
        self._saved = None

    def __enter__(self):
        self._saved = sys.modules.get("pythoncom", "__absent__")
        sys.modules["pythoncom"] = self.fake
        return self.fake

    def __exit__(self, *exc) -> None:
        if self._saved == "__absent__":
            sys.modules.pop("pythoncom", None)
        else:
            sys.modules["pythoncom"] = self._saved


class IdlePumpTest(unittest.TestCase):
    def test_pump_is_noop_without_pythoncom(self) -> None:
        """No pywin32 (Linux/CI) -> the pump must return 0, not raise."""
        with _FakePythoncomContext(None):
            session = CybosPlus()
            self.assertEqual(session.pump_waiting_messages(), 0)

    def test_pump_drains_available_messages(self) -> None:
        fake = _FakePythoncom(pending=5)
        with _FakePythoncomContext(fake):
            session = CybosPlus()
            self.assertEqual(session.pump_waiting_messages(), 5)
            self.assertEqual(fake.pump_calls, 6)  # 5 dispatched + 1 empty

    def test_pump_is_bounded(self) -> None:
        """A permanently non-empty message queue must not starve queued jobs."""
        fake = _FakePythoncom(always=True)
        with _FakePythoncomContext(fake):
            session = CybosPlus()
            self.assertEqual(session.pump_waiting_messages(), _MAX_IDLE_PUMP)

    def test_worker_pumps_while_idle(self) -> None:
        """The blocking worker must pump messages while it waits for jobs."""
        fake = _FakePythoncom(always=True)
        with _FakePythoncomContext(fake):
            session, _fakes = support.fake_session()
            try:
                self.assertTrue(session.is_connected())  # starts the worker
                worker = session._thread
                self.assertIsNotNone(worker)
                time.sleep(0.25)
                self.assertGreater(
                    fake.calls_on(worker),
                    2,
                    "worker thread never pumped COM messages while idle",
                )
            finally:
                session.stop()

    def test_worker_stops_pumping_after_stop(self) -> None:
        fake = _FakePythoncom(always=True)
        with _FakePythoncomContext(fake):
            session, _fakes = support.fake_session()
            self.assertTrue(session.is_connected())
            worker = session._thread
            self.assertIsNotNone(worker)
            time.sleep(0.1)

            session.stop()
            time.sleep(0.1)
            settled = fake.calls_on(worker)
            time.sleep(0.15)
            self.assertFalse(worker.is_alive(), "worker thread outlived stop()")
            self.assertEqual(
                fake.calls_on(worker), settled, "stopped worker kept pumping"
            )


if __name__ == "__main__":
    unittest.main()
