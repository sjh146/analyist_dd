"""Journal tests: record/close/summary round-trip and migration idempotence."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from trader_core import Journal

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 4, 10, 0, tzinfo=KST)
# summary() reference: safely after every synthetic exit timestamp below
REF = NOW + timedelta(days=10)


class JournalTest(unittest.TestCase):
    def test_record_trade_and_close_long(self) -> None:
        journal = Journal(":memory:")
        trade_id = journal.record_trade(
            side="buy", code="005930", name="Samsung", qty=100, price=1000.0,
            order_type="limit", entry_reason="signal",
            stop_pct=0.07, tp_pct=0.15, ts=NOW,
        )
        self.assertGreater(trade_id, 0)
        ok = journal.close_trade(
            trade_id, exit_price=1100.0, exit_reason="tp", fees=0.0,
            exit_ts=NOW + timedelta(days=2),
        )
        self.assertTrue(ok)
        summary = journal.summary(now=REF)
        self.assertEqual(summary["trades"], 1)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["total_pnl"], 100 * 100.0)

    def test_close_trade_short_pnl_inverted(self) -> None:
        journal = Journal(":memory:")
        trade_id = journal.record_trade(
            side="sell", code="000660", qty=10, price=5000.0, ts=NOW,
        )
        journal.close_trade(trade_id, exit_price=4500.0, exit_reason="buy-back")
        summary = journal.summary(now=REF)
        # sold at 5000, covered at 4500 -> profit (5000-4500)*10
        self.assertEqual(summary["total_pnl"], 5000.0)

    def test_summary_wins_losses_winrate(self) -> None:
        journal = Journal(":memory:")
        for i, (entry, exit_price) in enumerate([(1000, 1200), (1000, 800),
                                                 (1000, 950)]):
            tid = journal.record_trade(
                side="buy", code="00593{0}".format(i), qty=10, price=entry,
                ts=NOW - timedelta(days=1),
            )
            journal.close_trade(
                tid, exit_price=exit_price, exit_reason="x",
                exit_ts=NOW - timedelta(hours=i),
            )
        summary = journal.summary(now=REF)
        self.assertEqual(summary["trades"], 3)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 2)
        self.assertAlmostEqual(summary["win_rate"], 1 / 3)
        self.assertEqual(summary["avg_win"], 2000.0)
        self.assertEqual(summary["avg_loss"], 1250.0)  # (2000+500)/2
        self.assertEqual(summary["total_pnl"], 2000 - 2000 - 500)

    def test_open_trade_lookup_and_close_invalid(self) -> None:
        journal = Journal(":memory:")
        tid = journal.record_trade(
            side="buy", code="005930", qty=10, price=1000.0, ts=NOW,
        )
        row = journal.open_trade_for("005930")
        self.assertIsNotNone(row)
        self.assertEqual(row["code"], "005930")
        self.assertFalse(journal.close_trade(99999, exit_price=1100.0))
        journal.close_trade(tid, exit_price=1100.0, exit_reason="tp")
        self.assertFalse(journal.close_trade(tid, exit_price=1200.0))
        self.assertIsNone(journal.open_trade_for("005930"))

    def test_trade_count_today(self) -> None:
        journal = Journal(":memory:")
        journal.record_trade(side="buy", code="005930", qty=1, price=1000,
                             ts=NOW)
        journal.record_trade(side="buy", code="000660", qty=1, price=1000,
                             ts=NOW - timedelta(days=1))
        self.assertEqual(journal.trade_count_today(now=NOW), 1)

    def test_decision_roundtrip(self) -> None:
        journal = Journal(":memory:")
        journal.record_decision(
            screener="close", code="005930", name="Samsung", score=90.0,
            gates_json='[{"gate":"feed_fresh","ok":true}]',
            plan_json='{"side":"buy"}', reason="ok", ts=NOW,
        )
        rows = journal.recent_decisions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "005930")
        self.assertEqual(rows[0]["reason"], "ok")

    def test_migration_idempotent_on_file(self) -> None:
        tmp = tempfile.mkdtemp(prefix="trader-core-journal-")
        try:
            path = os.path.join(tmp, "j.sqlite3")
            journal = Journal(path)
            journal.record_trade(side="buy", code="005930", qty=1, price=1000,
                                 ts=NOW)
            version = journal._conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, 4)  # v2 screener, v3 hwm, v4 algo
            journal.close()
            journal2 = Journal(path)  # re-open: migration must be a no-op
            self.assertEqual(
                journal2._conn.execute("PRAGMA user_version").fetchone()[0], 4
            )
            self.assertEqual(len(journal2.open_trades()), 1)
            # v1 -> v2 upgrade path: the column exists exactly once and old rows
            # read back with an empty screener rather than blowing up.
            columns = [
                row["name"]
                for row in journal2._conn.execute("PRAGMA table_info(trades)")
            ]
            self.assertEqual(columns.count("screener"), 1)
            self.assertEqual(journal2.open_trades()[0]["screener"], "")
            journal2.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_default_journal_path_directory_created(self) -> None:
        tmp = tempfile.mkdtemp(prefix="trader-core-journal-dir-")
        try:
            path = os.path.join(tmp, "nested", "trade_journal.sqlite3")
            journal = Journal(path)
            journal.record_decision(screener="close", code="005930", ts=NOW)
            self.assertTrue(os.path.exists(path))
            journal.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
