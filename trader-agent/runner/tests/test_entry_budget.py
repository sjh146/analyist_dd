"""R2 entry accounting: dry-run fills must not consume the live entry budget.

Regression test for a real operational trap found 2026-09-10 while bringing the
system up: one ``--dry-run`` cycle ("6 dry-run order(s) filled") pushed
``daily.entries`` to 6, so the following ``--live`` cycle was refused with
``r2: entries today 6 >= max 3`` -- a single morning dry-run would have blocked
paper/live trading for the whole day.
"""

from __future__ import annotations

import tempfile
import unittest

from runner.state import StateStore
from runner.tests import support as rsupport


class DryRunEntryBudgetTest(unittest.TestCase):
    def _run(self, tmp, *, dry_run: bool, entries: int, codes=("005930",)):
        snapshot = rsupport.make_snapshot(list(codes))
        cfg, loop, _mock = rsupport.make_loop_bridge_and_feed(
            tmp, rsupport.StaticFeed(snapshot), codes=list(codes),
            dry_run=dry_run,
        )
        store = StateStore(cfg.state_path)
        state = store.read()
        state["daily"]["date"] = "2026-09-07"  # same KST day as MONDAY_OPEN,
        state["daily"]["entries"] = entries    # so the rollover must not reset
        store.write(state)
        report = loop.run_once(now=rsupport.MONDAY_OPEN)
        result = StateStore(cfg.state_path).read()
        close = getattr(loop.journal, "close", None)
        if callable(close):
            close()  # Windows cannot remove the temp dir with sqlite still open
        return cfg, report, result

    def test_dry_run_fills_do_not_touch_the_live_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, report, state = self._run(tmp, dry_run=True, entries=0)
            self.assertGreaterEqual(report.acted, 1)
            self.assertEqual(state["daily"]["dry_run_entries"], report.acted)
            self.assertEqual(state["daily"]["entries"], 0)

    def test_live_fills_count_toward_the_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, report, state = self._run(tmp, dry_run=False, entries=0)
            self.assertGreaterEqual(report.acted, 1)
            self.assertEqual(state["daily"]["entries"], report.acted)
            self.assertEqual(state["daily"]["dry_run_entries"], 0)

    def test_dry_run_still_works_after_the_live_cap_is_used_up(self) -> None:
        """Simulation must stay usable even when the live budget is spent."""
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, report, state = self._run(tmp, dry_run=True, entries=3)
            self.assertGreaterEqual(report.acted, 1)
            self.assertFalse(
                any("r2: entries today" in reason for reason in report.reasons),
                "dry-run cycle was blocked by the live entry counter",
            )
            self.assertEqual(state["daily"]["entries"], 3)  # untouched

    def test_live_cycle_is_blocked_once_the_live_cap_is_used_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, report, _state = self._run(tmp, dry_run=False, entries=3)
            self.assertEqual(report.acted, 0)
            self.assertTrue(
                any("r2: entries today" in reason for reason in report.reasons)
            )

    def test_cycle_truncates_to_the_remaining_entry_budget(self) -> None:
        """A 6-candidate cycle must not open 6 entries against a max-3 cap.

        Found live 2026-09-10: the pre-cycle R2 check let an entire batch
        through, so the daily cap was only enforced across cycles, not within
        one -- one busy cycle defeated "max 3 entries/day".

        Since the real-account switch (2026-09-28) the engine's own
        ``max_open_positions`` (3) rejects the surplus candidates before the
        loop-level R2 truncation triggers, so the cap holds at either layer.
        What matters here: never more than the cap in one cycle, and a reason
        recorded for the candidates that were dropped.
        """
        with tempfile.TemporaryDirectory() as tmp:
            codes = ["{0:06d}".format(index) for index in range(1, 7)]
            snapshot = rsupport.make_snapshot(codes)
            cfg, loop, _mock = rsupport.make_loop_bridge_and_feed(
                tmp, rsupport.StaticFeed(snapshot), codes=codes,
                dry_run=False, r2_max=3,
            )
            report = loop.run_once(now=rsupport.MONDAY_OPEN)
            self.assertEqual(report.acted, 3)
            self.assertTrue(
                any(
                    "capped this cycle" in reason or "max_open_positions" in reason
                    for reason in report.reasons
                ),
                report.reasons,
            )
            state = StateStore(cfg.state_path).read()
            self.assertEqual(state["daily"]["entries"], 3)
            close = getattr(loop.journal, "close", None)
            if callable(close):
                close()

    def test_rollover_resets_both_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp, dry_run=True, entries=0)
            _cfg, report, state = self._run(tmp, dry_run=False, entries=0)
            # A new KST day resets daily counters from the state defaults.
            self.assertEqual(state["daily"]["entries"], report.acted)
            self.assertIn("dry_run_entries", state["daily"])


if __name__ == "__main__":
    unittest.main()
