"""Transient COM-failure retry (DibRq "다시 요청하십시오").

Live incident: ``/positions`` and ``/balance`` started answering
``server_error: (-2147352567, 'DibRq 통신요청 실패하였습니다. 다시 요청하십시오.')``
and kept failing, because ``BlockRequest()`` raised a COM exception that nothing
caught -- ``block_until_ok`` only retried the ``ret == 4`` *return code*, never
an exception.  The broker explicitly asks for a re-request, so the bridge now
retries a few times and then surfaces the error.
"""

from __future__ import annotations

import unittest

from bridge.plus import BridgeError, _is_transient_com_error
from bridge.tests import support

DIB_MESSAGE = (
    "(-2147352567, '예외가 발생했습니다.', (0, None, "
    "'DibRq 통신요청 실패하였습니다. 다시 요청하십시오.(RQID:0)', None, 0, "
    "-2147467259), None)"
)


class TransientClassifierTest(unittest.TestCase):
    def test_dib_message_is_transient(self) -> None:
        self.assertTrue(_is_transient_com_error(RuntimeError(DIB_MESSAGE)))

    def test_hrresult_only_is_transient(self) -> None:
        self.assertTrue(
            _is_transient_com_error(RuntimeError(-2147352567, "no text"))
        )

    def test_unrelated_errors_are_not_transient(self) -> None:
        self.assertFalse(_is_transient_com_error(RuntimeError("disk full")))
        self.assertFalse(_is_transient_com_error(ValueError("bad input")))

    def test_bridge_error_is_never_retried(self) -> None:
        self.assertFalse(
            _is_transient_com_error(BridgeError("remote_error", "94007 매매불가"))
        )


class BlockRequestRetryTest(unittest.TestCase):
    def _session_with(self, fail_times: int):
        """Return ``(session, obj)`` whose BlockRequest raises *fail_times*."""
        wc = support.client()
        wc.reset()
        cybos = wc.make("CpUtil.CpCybos")
        cybos.IsConnect = 1
        cybos.LimitRequestRemainTime = 0
        obj = wc.make("CpTrade.CpTd6033")
        obj.headers[0] = 0  # no rows: we only care about the retry behaviour

        calls = {"n": 0}
        original = obj.BlockRequest

        def flaky():
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise RuntimeError(DIB_MESSAGE)
            return original()

        obj.BlockRequest = flaky
        session = support.CybosPlus()
        return session, obj, calls

    def test_retries_then_succeeds(self) -> None:
        session, obj, calls = self._session_with(fail_times=2)
        session.block_until_ok(obj)
        self.assertEqual(calls["n"], 3)  # two failures + one success

    def test_gives_up_after_the_retry_budget(self) -> None:
        session, obj, calls = self._session_with(fail_times=99)
        with self.assertRaises(RuntimeError):
            session.block_until_ok(obj)
        self.assertEqual(calls["n"], 4)  # first try + 3 retries

    def test_non_transient_error_is_not_retried(self) -> None:
        wc = support.client()
        wc.reset()
        obj = wc.make("CpTrade.CpTd6033")

        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise BridgeError("remote_error", "94007 매매불가 종목입니다")

        obj.BlockRequest = boom
        session = support.CybosPlus()
        with self.assertRaises(BridgeError):
            session.block_until_ok(obj)
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
