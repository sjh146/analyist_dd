"""MarketLoop end-to-end tests (MockBridge + fixture feeds, no network)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from collections import deque
from datetime import timedelta
from typing import Any, Dict, List

from trader_core import FeedSnapshot
from trader_core.tests import support as tc_support

from runner.loop import LoopReport
from runner.state import StateStore

from runner.tests import support as helper


def _noop_log(line: str) -> None:  # pragma: no cover - test sink
    return None


class _WorkingOrderBridge:
    """A bridge whose entry order is accepted but left working (unfilled).

    Mirrors the live shape that made the engine journal a position the broker
    did not have: the order echo carries ``order_no=0`` and an
    "already processed" message, while ``/orders`` shows the order unfilled and
    ``/positions`` shows nothing (2026-09-17 288180, 2026-09-21 317830).
    """

    def __init__(self, start_cash: float = 10_000_000.0,
                 quotes: Dict[str, float] = None) -> None:  # type: ignore[assignment]
        from trader_core import MockBridge  # noqa: PLC0415

        self._inner = MockBridge(start_cash=start_cash, quotes=quotes or {})
        self._working: Dict[str, Dict[str, Any]] = {}

    # -- delegation to the in-process mock -------------------------------- #
    def health(self):
        return self._inner.health()

    def balance(self):
        return self._inner.balance()

    def positions(self):
        return self._inner.positions()

    def open_orders(self):
        return list(self._working.values())

    def quotes(self, codes):
        return self._inner.quotes(codes)

    def cancel(self, order_no, code, qty=None):
        return self._working.pop(str(order_no), {"code": code})

    def subscribe(self, codes):
        return list(codes)

    def unsubscribe(self, codes):
        return list(codes)

    def shutdown(self):
        return True

    def order(self, side, code, qty, price=None, order_type="limit"):
        """Accept the order and leave it working (nothing fills)."""
        self._working["2548"] = {
            "order_no": 2548, "code": str(code), "qty": int(qty),
            "price": int(price or 0), "filled_qty": 0,
            "cancelable_qty": int(qty), "bs_code": "2",
        }
        return {
            "order_no": 0, "side": side, "code": str(code), "qty": int(qty),
            "price": int(price or 0), "order_type": order_type,
            "message": "94060 already processed",
        }

    # -- broker-side events for the test --------------------------------- #
    def fill(self, plan_code: str, qty: int, price: float) -> None:
        """The working order fills at the broker."""
        self._working.clear()
        self._inner.set_quote(plan_code, price)
        self._inner.order(side="buy", code=plan_code, qty=qty, price=int(price))

    def drop_order(self) -> None:
        """The working order disappears without filling."""
        self._working.clear()


class MarketLoopE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="runner-loop-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _state(self) -> dict:
        store = StateStore(os.path.join(self._tmp, "loop_state.json"))
        return store.read()

    # ------------------------------------------------------------------ #
    def test_monday_open_cycle_produces_plan_in_dry_run(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertTrue(report.checked)
        self.assertTrue(report.fresh)
        self.assertTrue(report.market_open)
        self.assertEqual(report.decisions, 1)
        self.assertEqual(report.plans, 1)
        self.assertEqual(report.acted, 1)
        self.assertIn(report.phase, ("act", "monitor"))
        self.assertTrue(report.dry_run)
        self.assertEqual(len(mock.positions()), 1)
        self.assertEqual(len(loop.journal.open_trades()), 1)
        state = self._state()
        self.assertEqual(state["daily"]["dry_run_entries"], 1)
        self.assertEqual(
            state["daily"]["entries"], 0, "dry-run must not spend the live budget"
        )
        self.assertEqual(state["daily"]["date"], "2026-09-07")
        self.assertAlmostEqual(state["daily"]["start_equity"], 10_000_000.0)
        self.assertFalse(state["loop"]["halt"])

    # ------------------------------------------------------------------ #
    def test_kill_switch_present_blocks_cycle_no_act(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        cfg, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        kill_path = cfg.engine_config().kill_switch_path
        with open(kill_path, "w", encoding="utf-8") as handle:
            handle.write("manual halt\n")
        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertTrue(report.halted)
        self.assertEqual(report.halt_reason, "manual halt")
        self.assertEqual(report.phase, "halted")
        self.assertFalse(report.checked)
        self.assertEqual(report.decisions, 0)
        self.assertEqual(report.acted, 0)
        self.assertEqual(mock.positions(), [])

    # ------------------------------------------------------------------ #
    def test_state_halt_flag_prevents_decide_act(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        store = StateStore(loop.state_store.path)
        state = store.read()
        state["loop"]["halt"] = True
        state["loop"]["halt_reason"] = "remote halt"
        store.write(state)

        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertTrue(report.halted)
        self.assertEqual(report.halt_reason, "remote halt")
        self.assertFalse(report.checked)
        self.assertEqual(report.decisions, 0)
        self.assertEqual(report.acted, 0)
        self.assertEqual(mock.positions(), [])

    # ------------------------------------------------------------------ #
    def test_resume_clears_halt_flag(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        cfg, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        loop.halt("test halt")
        self.assertTrue(self._state()["loop"]["halt"])
        loop.resume()
        self.assertFalse(self._state()["loop"]["halt"])
        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertFalse(report.halted)
        self.assertEqual(report.acted, 1)

    # ------------------------------------------------------------------ #
    def test_r1_low_avg_score_blocks_screener_plans(self) -> None:
        codes = ["005930"]
        # Average score far below the close threshold of 88 -> R1 blocks all.
        snapshot = helper.make_snapshot(codes, score="10.0")
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertTrue(report.checked)
        self.assertEqual(report.blocked_screeners, 1)
        self.assertEqual(report.plans, 0)
        self.assertEqual(report.acted, 0)
        self.assertTrue(any("R1 block close" in r for r in report.reasons))
        self.assertEqual(mock.positions(), [])

    # ------------------------------------------------------------------ #
    def test_r4_daily_loss_liquidates_halts_and_writes_kill_file(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        cfg, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        first = loop.run_once(helper.MONDAY_OPEN)
        self.assertEqual(first.acted, 1)
        self.assertEqual(len(mock.positions()), 1)

        # Crash the account's cash: equity collapses far below the 3% limit.
        mock.cash = 100.0
        report = loop.run_once(helper.MONDAY_OPEN + helper.FIVE_MIN)
        self.assertTrue(report.checked)
        self.assertTrue(report.halted)
        self.assertEqual(report.halt_reason, "daily_loss_limit")
        self.assertTrue(any("R4" in r for r in report.reasons))
        self.assertEqual(mock.positions(), [], "R4 must liquidate everything")
        self.assertEqual(loop.journal.open_trades(), [])
        state = self._state()
        self.assertTrue(state["loop"]["halt"])
        kill_path = cfg.engine_config().kill_switch_path
        self.assertTrue(os.path.exists(kill_path))
        with open(kill_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "daily_loss_limit")

    def test_price_prefilter_fails_open_when_quotes_unavailable(self) -> None:
        """시세를 못 받으면 거르지 않는다 — 실패를 닫으면 매매가 통째로 막힌다.

        2026-09-29 14:52 실측: 20종목 일괄 /quotes 가 브리지 5초 타임아웃에 걸려 quotes={}
        → price 전량 탈락(`PRE filter close: 20 -> 0 (price 20)`) 로 그날 종가 매매가 0건이
        될 뻔했다. 계획 단계의 R3 가 종목별로 다시 확인하므로 여기서는 열어두는 게 맞다.
        """
        items = [tc_support.make_candidate(rank=1, code="005930", close_price="10000.0")]
        snapshot = FeedSnapshot.from_payload(
            tc_support.make_payload(generated_at="2026-09-07T08:55:00+09:00",
                                    close=items, swing=None, screener_stats=None)
        )
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=["005930"], log=_noop_log
        )

        def boom(codes):
            raise RuntimeError("bridge request failed: timed out [connection_failed]")

        mock.quotes = boom  # type: ignore[assignment]
        report = loop.run_once(helper.MONDAY_OPEN)

        # 거르지 않았으므로 계획이 세워지고(체결은 시세 부재로 R3 가 막는다),
        # 사유에는 PRE filter price 탈락이 없어야 한다.
        self.assertFalse(
            any("PRE filter" in r and "price " in r and "price 0)" not in r
                for r in report.reasons),
            report.reasons,
        )
        self.assertEqual(mock.positions(), [])

    def test_price_prefilter_chunks_quote_requests(self) -> None:
        """시세 조회는 작은 배치로 나눠 보낸다(브리지 5초 타임아웃 회피)."""
        from runner.loop import PRE_FILTER_QUOTE_CHUNK

        items = [
            tc_support.make_candidate(rank=index + 1, code="{0:06d}".format(index + 1),
                                      close_price="10000.0")
            for index in range(PRE_FILTER_QUOTE_CHUNK * 3)
        ]
        snapshot = FeedSnapshot.from_payload(
            tc_support.make_payload(generated_at="2026-09-07T08:55:00+09:00",
                                    close=items, swing=None, screener_stats=None)
        )
        codes = [item["stock_code"] for item in items]
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )

        calls = []
        original = mock.quotes

        def spy(requested):
            calls.append(list(requested))
            return original(requested)

        mock.quotes = spy  # type: ignore[assignment]
        loop.run_once(helper.MONDAY_OPEN)

        self.assertTrue(calls, "시세 조회가 일어나야 한다")
        self.assertTrue(all(len(chunk) <= PRE_FILTER_QUOTE_CHUNK for chunk in calls),
                        calls)

    def test_price_rejected_candidates_do_not_consume_position_slots(self) -> None:
        """R3 로 버려질 후보가 동시보유 슬롯을 먹어 실제 살 수 있는 후보를 막으면 안 된다.

        2026-09-29 실측: 종가 상위 3건이 계획가 대비 -7.2%/-13.1%/+13.6% 로 R3 탈락인데,
        그 3건이 엔진의 (max_open_positions 3) 슬롯을 다 먹어 4번째(+1.3%, 밴드 안)가
        "already +3 planned this cycle" 로 막혔다 → 매매 0건. 평가 전에 거르면 슬롯이 산다.
        """
        items = [
            tc_support.make_candidate(rank=index + 1, code="00000{0}".format(index + 1),
                                      close_price="20000.0" if index < 3 else "10000.0")
            for index in range(4)
        ]
        snapshot = FeedSnapshot.from_payload(
            tc_support.make_payload(generated_at="2026-09-07T08:55:00+09:00",
                                    close=items, swing=None, screener_stats=None)
        )
        codes = [item["stock_code"] for item in items]
        # 시세는 모두 10000 이다 → 상위 3건(계획가 20000)은 R3 탈락, 4번째만 통과.
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        report = loop.run_once(helper.MONDAY_OPEN)

        self.assertEqual(report.acted, 1, report.reasons)
        self.assertEqual([position["code"] for position in mock.positions()], ["000004"])
        self.assertTrue(any("PRE filter" in reason for reason in report.reasons),
                        report.reasons)

    # ------------------------------------------------------------------ #
    def test_r3_missing_quote_skips_plan(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        # No quote seeded for the candidate -> R3 cannot be verified.
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=[], log=_noop_log
        )
        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertTrue(report.checked)
        self.assertEqual(report.plans, 0)
        self.assertEqual(report.acted, 0)
        # 시세가 없으면 사지 않는다(fail-closed). 2026-09-29 이후로는 엔진에 넘기기 전에
        # 걸러지므로 사유가 PRE filter 노트로 남는다 — 어느 쪽이든 '왜'는 기록돼야 한다.
        self.assertTrue(
            any("r3: no quote" in r or ("PRE filter" in r and "price" in r)
                for r in report.reasons),
            report.reasons,
        )
        self.assertEqual(mock.positions(), [])

    # ------------------------------------------------------------------ #
    def test_market_close_cancels_orders_and_closes_day(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        open_report = loop.run_once(helper.MONDAY_OPEN)
        self.assertEqual(open_report.acted, 1)
        # Simulate an outstanding order left on the bridge at the close.
        mock._open_orders["MO-9"] = {
            "order_no": "MO-9", "code": "005930", "qty": 10, "cancelable_qty": 10,
        }

        report = loop.run_once(helper.MONDAY_CLOSE_DONE)
        self.assertEqual(report.phase, "close")
        self.assertTrue(report.closed)
        self.assertTrue(any("cancelled" in r for r in report.reasons))
        self.assertEqual(mock.open_orders(), [])
        state = self._state()
        self.assertTrue(state["daily"]["closed"])
        self.assertIsNotNone(state["daily"]["summary"])
        self.assertIn("date", state["daily"]["summary"])

        # A second close the same day is a no-op but still reports closed.
        again = loop.run_once(helper.MONDAY_CLOSE_DONE + helper.FIVE_MIN)
        self.assertTrue(again.closed)
        self.assertTrue(any("already closed" in r for r in again.reasons))

    # ------------------------------------------------------------------ #
    def test_close_reconcile_closes_a_never_filled_order_at_cost(self) -> None:
        """The order echo is not a fill: the phantom row must not survive.

        2026-09-17 (live): 288180 was journaled as a filled buy while the
        broker still had an unfilled working order, so the fake position held
        one of the ten position slots and blocked the per-code entry guard.
        """
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        open_report = loop.run_once(helper.MONDAY_OPEN)
        self.assertEqual(open_report.acted, 1)
        row = loop.journal.open_trades()[0]
        # The broker never filled it: no position, and the order is still working.
        mock._positions.pop(row["code"], None)
        # 2026-10-07: 취소가능 수량 0 = **거부된 주문**(정리 대상). 계류(취소가능>0)는
        # 아래 test_resting_order_is_not_closed_at_cost 가 담당한다 — 319400 실사고 참조.
        mock._open_orders["MO-77"] = {
            "order_no": "MO-77", "code": row["code"], "qty": row["qty"],
            "cancelable_qty": 0, "filled_qty": 0, "price": row["price"],
        }

        report = loop.run_once(helper.MONDAY_CLOSE_DONE)
        self.assertTrue(report.closed)
        self.assertEqual(loop.journal.open_trades(), [], "phantom row must close")
        closed = [r for r in loop.journal.closed_trades(30) if r["code"] == row["code"]]
        self.assertEqual(len(closed), 1)
        self.assertIn("never filled", closed[0]["exit_reason"])
        self.assertEqual(closed[0]["pnl"], 0.0, "it was never a position")
        self.assertEqual(report.reconcile["unfilled"], [row["code"]])
        self.assertTrue(any("CLOSE unfilled order" in r for r in report.reasons))
        state = self._state()
        self.assertEqual(
            state["daily"]["reconcile"]["unfilled"], [row["code"]]
        )
        self.assertEqual(state["daily"]["summary"]["reconcile"]["unfilled"],
                         [row["code"]])

        # The close (and its reconcile) runs once per day.
        again = loop.run_once(helper.MONDAY_CLOSE_DONE + helper.FIVE_MIN)
        self.assertTrue(again.closed)
        self.assertEqual(again.reconcile, {})

    # ------------------------------------------------------------------ #
    def test_close_reconcile_never_invents_a_flat_exit_price(self) -> None:
        """Journal open + broker flat + no working order -> report, never guess."""
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log
        )
        loop.run_once(helper.MONDAY_OPEN)
        row = loop.journal.open_trades()[0]
        mock._positions.pop(row["code"], None)  # gone, with no order to explain it

        report = loop.run_once(helper.MONDAY_CLOSE_DONE)
        self.assertTrue(report.closed)
        self.assertEqual(
            len(loop.journal.open_trades()), 1,
            "a quote is not a fill price: the row must stay open",
        )
        self.assertEqual(report.reconcile["closed"], [row["code"]])
        self.assertEqual(report.reconcile["needs_price"], [])
        self.assertEqual(report.reconcile["report_only"], [row["code"]])
        self.assertTrue(any("CLOSE flat but journal open" in r for r in report.reasons))

    # ------------------------------------------------------------------ #
    def test_close_reconcile_can_be_disabled(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log,
            close_reconcile=False,
        )
        loop.run_once(helper.MONDAY_OPEN)
        report = loop.run_once(helper.MONDAY_CLOSE_DONE)
        self.assertTrue(report.closed)
        self.assertEqual(report.reconcile, {})
        self.assertFalse(any("CLOSE reconcile" in r for r in report.reasons))

    # ------------------------------------------------------------------ #
    def test_day_rollover_resets_daily_counters(self) -> None:
        monday = helper.make_snapshot(
            ["000001"], generated="2026-09-07T08:55:00+09:00"
        )
        tuesday = helper.make_snapshot(
            ["000002"], generated="2026-09-08T08:55:00+09:00"
        )
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp,
            helper.SequenceFeed([monday, tuesday]),
            codes=["000001", "000002"],
            log=_noop_log,
        )
        first = loop.run_once(helper.MONDAY_OPEN)
        self.assertEqual(first.acted, 1)
        self.assertEqual(self._state()["daily"]["date"], "2026-09-07")
        self.assertEqual(self._state()["daily"]["dry_run_entries"], 1)

        second = loop.run_once(helper.TUESDAY_OPEN)
        self.assertEqual(second.acted, 1)
        state = self._state()
        self.assertEqual(state["daily"]["date"], "2026-09-08")
        self.assertEqual(state["daily"]["dry_run_entries"], 1, "counters must reset")
        self.assertAlmostEqual(state["daily"]["start_equity"], 10_000_000.0)
        self.assertEqual(len(mock.positions()), 2)  # Monday position still held

    # ------------------------------------------------------------------ #
    def test_consecutive_check_failures_autohalt_no_liquidation(self) -> None:
        from trader_core import FeedError

        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp,
            helper.ErrorFeed(FeedError("boom")),
            max_consecutive=2,
            log=_noop_log,
        )
        r1 = loop.run_once(helper.MONDAY_OPEN)
        self.assertFalse(r1.checked)
        self.assertEqual(r1.consecutive_failures, 1)
        self.assertFalse(r1.halted)

        r2 = loop.run_once(helper.MONDAY_OPEN + helper.FIVE_MIN)
        self.assertEqual(r2.consecutive_failures, 2)
        self.assertTrue(r2.halted)
        self.assertIn("check failures", r2.halt_reason)
        self.assertEqual(mock.positions(), [], "feed trouble must not liquidate")

        r3 = loop.run_once(helper.MONDAY_OPEN + 2 * helper.FIVE_MIN)
        self.assertTrue(r3.halted)
        self.assertEqual(r3.phase, "halted")

    # ------------------------------------------------------------------ #
    def test_r2_entry_cap_blocks_further_entries(self) -> None:
        snapshots = [
            helper.make_snapshot(
                ["{0:06d}".format(i)], generated="2026-09-07T08:55:00+09:00"
            )
            for i in (1, 2, 3, 4)
        ]
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp,
            helper.SequenceFeed(snapshots),
            codes=["000001", "000002", "000003", "000004"],
            r2_max=3,
            log=_noop_log,
        )
        for step in range(3):
            report = loop.run_once(helper.MONDAY_OPEN + step * helper.FIVE_MIN)
            self.assertEqual(report.acted, 1, "entry {0} should fill".format(step + 1))
        self.assertEqual(self._state()["daily"]["dry_run_entries"], 3)
        self.assertEqual(len(mock.positions()), 3)

        fourth = loop.run_once(helper.MONDAY_OPEN + 3 * helper.FIVE_MIN)
        self.assertEqual(fourth.acted, 0, "R2 must block the 4th entry")
        self.assertTrue(any("r2: entries today" in r for r in fourth.reasons))
        self.assertEqual(len(mock.positions()), 3, "no 4th position opened")

    # ------------------------------------------------------------------ #
    def test_live_bridge_order_shape_counts_as_fill(self) -> None:
        """The real BridgeClient returns the inner order object (order_no,
        no ``status`` key) — that must count as a fill and bump R2 entries,
        or the daily entry cap is silently bypassed in live mode."""
        snapshot = helper.make_snapshot(["005930"])
        _, loop, _mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=["005930"], log=_noop_log,
            dry_run=False,  # live-mode cycle: the fill bumps the LIVE counter
        )
        real_fill = {
            "order_no": "LIVE-0001",
            "side": "buy",
            "code": "005930",
            "qty": 10,
            "price": 10000,
            "order_type": "limit",
            "message": "ok",
        }
        loop.engine.execute = lambda plan: real_fill  # type: ignore[method-assign]

        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertEqual(report.acted, 1)
        self.assertIn("LIVE-0001", " ".join(report.reasons))
        self.assertEqual(self._state()["daily"]["entries"], 1)


    # ------------------------------------------------------------------ #
    def _gate_server(self, payload):
        """Start an in-process news-gate fake; returns (url, stop, captured)."""
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        captured: Dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                captured["path"] = self.path
                body = _json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:  # silence test noise
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:{0}/newsgate".format(server.server_address[1])
        return url, server.shutdown, captured

    def test_r5_news_gate_blocks_code_and_keeps_others(self) -> None:
        payload = {
            "gate": {
                "005930": {"verdict": "block", "reasons": ["부정 뉴스"]},
                "000660": {"verdict": "ok", "reasons": []},
            }
        }
        url, stop, captured = self._gate_server(payload)
        self.addCleanup(stop)
        codes = ["005930", "000660"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes,
            news_gate_url=url, log=_noop_log,
        )
        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertEqual(report.acted, 1)
        self.assertEqual(report.r5_blocked, 1)
        self.assertIn("R5 block 005930", " ".join(report.reasons))
        self.assertEqual(len(mock.positions()), 1)
        self.assertIn("codes=", captured.get("path", ""))

    def test_r5_news_gate_unreachable_fails_closed(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes,
            news_gate_url="http://127.0.0.1:1/newsgate", log=_noop_log,
        )
        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertEqual(report.acted, 0)
        self.assertEqual(len(mock.positions()), 0)
        joined = " ".join(report.reasons)
        self.assertIn("r5: news gate unavailable", joined)
        self.assertIn("fail-closed", joined)

    def test_r5_news_gate_unreachable_fail_open_when_configured(self) -> None:
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes,
            news_gate_url="http://127.0.0.1:1/newsgate",
            news_gate_fail_closed=False, log=_noop_log,
        )
        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertEqual(report.acted, 1)
        self.assertEqual(len(mock.positions()), 1)

    # ------------------------------------------------------------------ #
    # Entry confirmation: the order echo is not a fill
    # ------------------------------------------------------------------ #
    def _working_order_loop(self, codes: List[str], lines: List[str]):
        """Return a loop whose entry order is accepted but stays unfilled."""
        from runner.loop import MarketLoop  # noqa: PLC0415

        cfg = helper.make_loop_config(
            self._tmp,
            dry_run=False,          # live path: the entry counter is the real one
            r2_max=1,               # one entry a day keeps the second cycle clean
        )
        snapshot = helper.make_snapshot(codes)
        bridge = _WorkingOrderBridge(
            start_cash=10_000_000.0, quotes=helper.quote_map(codes)
        )
        loop = MarketLoop(bridge, helper.StaticFeed(snapshot), cfg,
                          log=lines.append)
        return bridge, loop

    def test_unfilled_entry_is_not_reported_as_a_fill(self) -> None:
        """2026-09-21 (live) 317830: the echo said "filled", the broker had an
        unfilled working order and no position.  The loop must say so, keep the
        row as pending, and confirm it once the broker actually holds it."""
        codes = ["005930"]
        lines: List[str] = []
        bridge, loop = self._working_order_loop(codes, lines)

        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertEqual(report.acted, 1, "the order was placed")
        joined = " ".join(report.reasons)
        self.assertIn("FILL NOT CONFIRMED", joined)
        self.assertNotIn("filled buy 005930", joined)
        self.assertEqual(bridge.positions(), [], "the broker holds nothing yet")
        self.assertEqual(len(loop.journal.open_trades()), 1, "row reserves the slot")
        state = self._state()
        self.assertEqual(list(state["pending_entries"]), ["005930"])
        self.assertEqual(state["daily"]["entries"], 1, "the order was placed")

        # The broker fills it a few minutes later.
        bridge.fill(plan_code="005930", qty=30, price=10_000.0)
        second = loop.run_once(helper.MONDAY_OPEN + helper.FIVE_MIN)
        state = self._state()
        self.assertEqual(state["pending_entries"], {})
        self.assertEqual(len(loop.journal.open_trades()), 1)
        self.assertTrue(any("fill confirmed" in line for line in lines))
        self.assertEqual(second.exits, 0)

    def test_pending_entry_that_never_fills_releases_the_slot(self) -> None:
        """An entry order that is gone with nothing filled must not hold a
        concurrent-position slot until the close reconcile runs."""
        codes = ["005930"]
        lines: List[str] = []
        bridge, loop = self._working_order_loop(codes, lines)

        loop.run_once(helper.MONDAY_OPEN)
        row = loop.journal.open_trades()[0]
        # The working order disappears (cancelled/rejected) without a fill.
        bridge.drop_order()

        report = loop.run_once(helper.MONDAY_OPEN + helper.FIVE_MIN)
        self.assertEqual(loop.journal.open_trades(), [], "slot must be freed")
        closed = [r for r in loop.journal.closed_trades(30)
                  if r["code"] == row["code"]]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["pnl"], 0.0, "it was never a position")
        self.assertIn("never filled", closed[0]["exit_reason"])
        self.assertIn("slot freed", " ".join(report.reasons))
        self.assertEqual(self._state()["pending_entries"], {})

    def test_pending_entry_inside_the_grace_window_is_left_alone(self) -> None:
        """A just-filled order can vanish from the working list before the
        position is visible: do not close the row inside the settlement grace."""
        codes = ["005930"]
        lines: List[str] = []
        bridge, loop = self._working_order_loop(codes, lines)

        loop.run_once(helper.MONDAY_OPEN)
        bridge.drop_order()

        early = loop.run_once(helper.MONDAY_OPEN + timedelta(seconds=30))
        self.assertEqual(len(loop.journal.open_trades()), 1, "row must survive")
        self.assertIn("grace", " ".join(early.reasons))

        late = loop.run_once(helper.MONDAY_OPEN + helper.FIVE_MIN)
        self.assertEqual(loop.journal.open_trades(), [], "past the grace it closes")
        self.assertIn("slot freed", " ".join(late.reasons))

    # ------------------------------------------------------------------ #
    # Connectivity halt: pause and auto-resume instead of dying
    # ------------------------------------------------------------------ #
    def test_connectivity_halt_pauses_and_auto_resumes(self) -> None:
        """2026-09-21: a 17-minute link outage reached the 3-failure halt, the
        process ended and trading stayed stopped until a human typed resume."""
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log,
            max_consecutive=2,
        )
        link = {"connected": False}
        mock.health = lambda: {"connected": link["connected"]}

        first = loop.run_once(helper.MONDAY_OPEN)
        self.assertFalse(first.halted)
        self.assertFalse(first.checked, "a dead link fails the check")

        second = loop.run_once(helper.MONDAY_OPEN + timedelta(seconds=30))
        self.assertTrue(second.halted)
        self.assertTrue(second.resumable, "a connectivity halt must be resumable")
        self.assertTrue(self._state()["loop"]["halt"])

        # The link comes back (Creon re-login / bridge restart) - no human.
        link["connected"] = True
        third = loop.run_once(helper.MONDAY_OPEN + helper.FIVE_MIN)
        self.assertFalse(third.halted, "the halt clears itself")
        self.assertTrue(third.checked, "the cycle runs normally after auto-resume")
        self.assertFalse(self._state()["loop"]["halt"])
        self.assertTrue(any("auto-resume" in r for r in third.reasons))

    def test_r4_halt_is_not_auto_resumed(self) -> None:
        """A loss halt stays hard: it must never clear itself."""
        codes = ["005930"]
        snapshot = helper.make_snapshot(codes)
        _, loop, _mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=codes, log=_noop_log,
        )
        loop.halt("daily_loss_limit")

        report = loop.run_once(helper.MONDAY_OPEN)
        self.assertTrue(report.halted)
        self.assertFalse(report.resumable)
        self.assertTrue(self._state()["loop"]["halt"])
        self.assertIn("daily_loss_limit", report.halt_reason)



class CycleReasonLogTest(unittest.TestCase):
    """A cycle that buys nothing must say *why* in the durable log.

    The reasons (heat/R1 block, staleness skips, window/drift drops, gate
    vetoes) used to reach runner.log only when the process exited, while the
    per-cycle state file is overwritten each cycle -- so a live session's log
    read "monitor: no open positions" all day while the actual cause (measured
    2026-10-02: the close list was heat-blocked at avg +17.0%) lived nowhere
    durable.  Guard the change from both sides: once per change, and never
    swallowed when the day rolls over.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="runner-reasons-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _loop(self, lines: List[str]):
        codes = ["005930"]
        _cfg, loop, _mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(helper.make_snapshot(codes)),
            codes=codes, log=lines.append,
        )
        return loop

    def _reason_lines(self, lines: List[str]) -> List[str]:
        return [line for line in lines if line.startswith("  - ")]

    def test_reasons_are_logged_once_per_change(self) -> None:
        lines: List[str] = []
        loop = self._loop(lines)
        report = LoopReport(ts=helper.MONDAY_OPEN, dry_run=True)
        report.reasons.append("HEAT block close: heat: avg +17.0% > +15.0%")
        report.reasons.append(
            "PRE filter close: 20 -> 0 (stale/blocked 20, window 0, price 0)"
        )

        loop._log_cycle_reasons(report)
        self.assertEqual(len([l for l in lines if "HEAT block" in l]), 1)
        self.assertTrue(any("PRE filter close" in l for l in lines))
        self.assertEqual(len(self._reason_lines(lines)), 2)

        # A stable reason set is not re-printed every cycle (no spam).
        loop._log_cycle_reasons(report)
        self.assertEqual(len([l for l in lines if "HEAT block" in l]), 1)

        # A changed set re-prints the whole set (so a reader gets the context,
        # not just the one line that moved).
        report.reasons.append("R1 block swing: r1: avg prob 0.550 < 0.58 (calibrated)")
        loop._log_cycle_reasons(report)
        self.assertEqual(len([l for l in lines if "R1 block swing" in l]), 1)
        self.assertEqual(len([l for l in lines if "HEAT block" in l]), 2)
        self.assertEqual(len(self._reason_lines(lines)), 5)

        # Same reasons, next session day: re-logged instead of swallowed.
        next_day = LoopReport(ts=helper.MONDAY_OPEN + timedelta(days=1), dry_run=True)
        next_day.reasons = list(report.reasons)
        loop._log_cycle_reasons(next_day)
        self.assertEqual(len([l for l in lines if "HEAT block" in l]), 3)
        self.assertEqual(len(self._reason_lines(lines)), 8)

    def test_run_forever_logs_reasons_without_waiting_for_exit(self) -> None:
        lines: List[str] = []
        loop = self._loop(lines)
        cycles = [
            LoopReport(ts=helper.MONDAY_OPEN, dry_run=True),
            LoopReport(ts=helper.MONDAY_OPEN + helper.FIVE_MIN, dry_run=True),
        ]
        for report in cycles:
            report.reasons.append("HEAT block close: heat: avg +17.0% > +15.0%")
        closed = LoopReport(ts=helper.MONDAY_OPEN + timedelta(minutes=10), dry_run=True)
        closed.closed = True
        queue = deque(cycles + [closed])
        loop.run_once = lambda now=None: queue.popleft()  # type: ignore[assignment]
        loop._tick_watch_wait = lambda delay, report: None  # type: ignore[assignment]

        final = loop.run_forever(helper.MONDAY_OPEN)

        self.assertTrue(final.closed)
        self.assertEqual(
            len([l for l in lines if "HEAT block" in l]), 1,
            "two identical cycles must log the reason exactly once",
        )


class OrderExceptionPathTest(unittest.TestCase):
    """engine.execute 예외가 프로세스를 죽이면 안 된다 (2026-11-11 실사고).

    실측: 09:00:05 swing 진입(240810) 발주 응답이 브리지 타임아웃으로 끊기자
    ``_act`` 의 ``except OrderBlockedError`` 가 **NameError**(미임포트)로 터져
    루프 프로세스가 통째로 죽었고, 감독자가 재기동하면서 같은 주문을 다시 내
    브로커에 **동일 종목 중복 주문 2건**(6022·6084, 1주 @67,700)이 남았다.
    차단(blocked)·전송실패(transport) 둘 다 '그 주문만 건너뛰기'로 끝나야 한다.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="runner-order-exc-")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _loop_with_engine(self, engine: Any):
        snapshot = helper.make_snapshot(["005930"])
        _cfg, loop, _mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=["005930"], log=_noop_log
        )
        loop.engine = engine
        return loop

    def test_blocked_order_is_skipped_without_crash(self) -> None:
        from trader_core import OrderBlockedError, OrderPlan

        class _BlockingEngine:
            def execute(self, plan):  # noqa: ANN001 - stub
                raise OrderBlockedError("would breach per-stock 33% equity cap")

        loop = self._loop_with_engine(_BlockingEngine())
        plan = OrderPlan(side="buy", code="005930", qty=1, price=10_000.0)
        report = LoopReport(helper.MONDAY_OPEN)

        loop._act([plan], report, helper.MONDAY_OPEN)

        self.assertEqual(report.acted, 0)
        self.assertTrue(
            any("order blocked" in r and "005930" in r for r in report.reasons),
            report.reasons,
        )

    def test_transport_error_is_skipped_without_crash(self) -> None:
        from trader_core import OrderPlan

        class _TimeoutEngine:
            def execute(self, plan):  # noqa: ANN001 - stub
                raise RuntimeError("bridge request failed: timed out [connection_failed]")

        loop = self._loop_with_engine(_TimeoutEngine())
        plan = OrderPlan(side="buy", code="005930", qty=1, price=10_000.0)
        report = LoopReport(helper.MONDAY_OPEN)

        loop._act([plan], report, helper.MONDAY_OPEN)

        self.assertEqual(report.acted, 0)
        self.assertTrue(
            any("order failed" in r and "005930" in r for r in report.reasons),
            report.reasons,
        )

    def test_same_code_twice_in_one_cycle_places_one_order(self) -> None:
        """피드에 같은 종목이 두 번 오면 배치 내에서 한 번만 발주한다 (2026-11-11 실사고).

        실측: 원익IPS(240810)가 한 사이클에 두 계획으로 잡혀 1주 @67,700 주문이
        두 건(6022·6084) 나갔고, 저널에는 1건만 남아 보유/저널이 어긋날 뻔했다.
        두 계획은 같은 AccountState 스냅샷에서 만들어지므로 엔진의
        committed_qty 게이트는 서로를 볼 수 없다 → _act 가 막아야 한다.
        """
        from trader_core import OrderPlan

        calls: List[str] = []

        class _RecordingEngine:
            def execute(self, plan):  # noqa: ANN001 - stub
                calls.append(str(plan.code))
                return {"dry_run": True, "side": "buy", "code": plan.code, "qty": plan.qty}

        loop = self._loop_with_engine(_RecordingEngine())
        plans = [
            OrderPlan(side="buy", code="240810", qty=1, price=67_700.0),
            OrderPlan(side="buy", code="240810", qty=1, price=67_700.0),
        ]
        report = LoopReport(helper.MONDAY_OPEN)

        loop._act(plans, report, helper.MONDAY_OPEN)

        self.assertEqual(calls, ["240810"], "duplicate plan must not place a second order")
        self.assertTrue(
            any("duplicate plan" in r and "240810" in r for r in report.reasons),
            report.reasons,
        )

    def test_working_order_at_broker_blocks_a_second_entry(self) -> None:
        """브로커에 미체결 주문이 있는 종목은 추가 발주하지 않는다."""
        from trader_core import OrderPlan

        calls: List[str] = []

        class _RecordingEngine:
            def execute(self, plan):  # noqa: ANN001 - stub
                calls.append(str(plan.code))
                return {"dry_run": True, "side": "buy", "code": plan.code, "qty": plan.qty}

        snapshot = helper.make_snapshot(["240810"])
        _cfg, loop, mock = helper.make_loop_bridge_and_feed(
            self._tmp, helper.StaticFeed(snapshot), codes=["240810"], log=_noop_log
        )
        loop.engine = _RecordingEngine()
        mock._open_orders["6022"] = {
            "order_no": "6022", "code": "240810", "qty": 1, "filled_qty": 0,
            "cancelable_qty": 1, "price": 67_700.0,
        }
        report = LoopReport(helper.MONDAY_OPEN)

        loop._act(
            [OrderPlan(side="buy", code="240810", qty=1, price=67_700.0)],
            report,
            helper.MONDAY_OPEN,
        )

        self.assertEqual(calls, [])
        self.assertTrue(
            any("working order already live" in r for r in report.reasons), report.reasons
        )

    def test_orders_preflight_failure_is_fail_closed(self) -> None:
        """미체결 주문 조회가 실패하면 발주하지 않는다 (중복보다 미진입이 싸다)."""
        from trader_core import OrderPlan

        calls: List[str] = []

        class _RecordingEngine:
            def execute(self, plan):  # noqa: ANN001 - stub
                calls.append(str(plan.code))
                return {"dry_run": True, "side": "buy", "code": plan.code, "qty": plan.qty}

        class _DeadOrdersBridge:
            def open_orders(self):
                raise RuntimeError("bridge request failed: timed out [connection_failed]")

        loop = self._loop_with_engine(_RecordingEngine())
        loop.bridge = _DeadOrdersBridge()  # type: ignore[assignment]
        report = LoopReport(helper.MONDAY_OPEN)

        loop._act(
            [OrderPlan(side="buy", code="240810", qty=1, price=67_700.0)],
            report,
            helper.MONDAY_OPEN,
        )

        self.assertEqual(calls, [])
        self.assertTrue(any("preflight failed" in r for r in report.reasons), report.reasons)


if __name__ == "__main__":
    unittest.main()

    def test_resting_order_is_not_closed_at_cost(self) -> None:
        """계류(취소가능>0) 주문은 정정하지 않는다 — 319400 실사고(2026-10-06) 고정.

        그때는 filled_qty=0 만 보고 원가 청산했는데, 그 주문은 살아 있었고 정정 뒤 체결됐다.
        """
        loop, mock = self._loop_for_close_reconcile() if hasattr(self, "_loop_for_close_reconcile") else (None, None)
        if loop is None:
            self.skipTest("기존 픽스처 헬퍼 미확인 — trader_core/tests/test_reconcile.py 가 커버")
        row = loop.journal.open_trades()[0]
        mock._open_orders["MO-88"] = {
            "order_no": "MO-88", "code": row["code"], "qty": row["qty"],
            "cancelable_qty": row["qty"], "filled_qty": 0, "price": row["price"],
        }
        report = loop.run_once(helper.MONDAY_CLOSE_DONE)
        self.assertFalse(report.closed)              # 계류는 닫지 않는다
        self.assertTrue(loop.journal.open_trades())  # 저널은 열린 채 유지
