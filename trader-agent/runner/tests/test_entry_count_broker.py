"""일일 진입 카운터가 **브로커 당일 체결**까지 세는지 고정한다.

WHY(2026-11-20 068050): 브리지가 타임아웃을 돌려줬지만 주문은 브로커에서 체결됐고
저널에 행이 남지 않았다. 저널 카운트만 쓰면 일일 상한 3건에 여유가 있는 것처럼
보여 **4번째 주문**이 나갔다. _entries_today() 는 max(저널, 브로커) 를 반환해야 한다.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest

from runner.loop import MarketLoop
from runner.tests import support as helper


class _BridgeWithDaily:
    """daily_pnl/open_orders 만 흉내내는 최소 브리지 (나머지는 MockBridge 위임)."""

    def __init__(self, items=None, open_orders=None, fail=False):
        from trader_core import MockBridge  # noqa: PLC0415

        self._inner = MockBridge(start_cash=10_000_000.0, quotes={})
        self._items = items or []
        self._open = open_orders or []
        self._fail = fail

    # MockBridge 위임 -------------------------------------------------- #
    def health(self):
        return self._inner.health()

    def balance(self):
        return self._inner.balance()

    def positions(self):
        return self._inner.positions()

    def quotes(self, codes):
        return self._inner.quotes(codes)

    # 이 테스트의 대상 ------------------------------------------------- #
    def daily_pnl(self):
        if self._fail:
            raise RuntimeError("bridge down")
        return {"summary": {}, "items": self._items}

    def open_orders(self):
        return list(self._open)


class EntryCounterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="entrycount-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _loop(self, bridge, entries_in_state: int = 0) -> MarketLoop:
        cfg = helper.make_loop_config(self._tmp, dry_run=False, r2_max=3)
        loop = MarketLoop(bridge, helper.StaticFeed(helper.make_snapshot(["005930"])), cfg)
        loop.state["daily"]["entries"] = entries_in_state
        return loop

    def test_broker_fills_raise_the_count_above_the_journal(self):
        bridge = _BridgeWithDaily(items=[
            {"code": "068050", "buy_qty": "74", "sell_qty": "0"},
            {"code": "362320", "buy_qty": "14", "sell_qty": "14"},
        ])
        self.assertEqual(self._loop(bridge, entries_in_state=1)._entries_today(), 2)

    def test_working_order_counts_even_without_a_fill(self):
        bridge = _BridgeWithDaily(items=[], open_orders=[
            {"code": "112040", "bs_code": "2", "filled_qty": 0},
        ])
        self.assertEqual(self._loop(bridge, entries_in_state=0)._entries_today(), 1)

    def test_journal_wins_when_it_is_larger(self):
        bridge = _BridgeWithDaily(items=[{"code": "068050", "buy_qty": "74"}])
        self.assertEqual(self._loop(bridge, entries_in_state=3)._entries_today(), 3)

    def test_bridge_failure_falls_back_to_journal(self):
        bridge = _BridgeWithDaily(fail=True)
        self.assertEqual(self._loop(bridge, entries_in_state=2)._entries_today(), 2)

    def test_sell_only_rows_do_not_count(self):
        bridge = _BridgeWithDaily(items=[{"code": "005930", "buy_qty": "0", "sell_qty": "5"}])
        self.assertEqual(self._loop(bridge, entries_in_state=0)._entries_today(), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
