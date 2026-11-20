"""tools/backfill_missing_entry.py — 저널 누락 체결 복원 도구 (3 tests).

2026-11-20 068050 사고(브리지 타임아웃 → 브로커 체결 → 저널 0행) 재발 시
감사·상한 정합을 사후 복원하는 경로를 고정한다.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.backfill_missing_entry import main as backfill_main  # noqa: E402
from trader_core.journal import Journal  # noqa: E402

TS = "2026-11-20T09:05:50+09:00"


class BackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="backfill-")
        self.db = str(Path(self.tmp) / "j.sqlite3")

    def test_dry_run_writes_nothing(self):
        rc = backfill_main(["--journal", self.db, "--code", "068050", "--qty", "74",
                            "--price", "1111.1351", "--entry-ts", TS])
        self.assertEqual(rc, 0)
        with Journal(self.db) as j:
            self.assertEqual(j.trade_count_today(_ts_dt()), 0)

    def test_apply_writes_buy_and_sell(self):
        rc = backfill_main(["--journal", self.db, "--code", "068050", "--qty", "74",
                            "--price", "1111.1351", "--entry-ts", TS,
                            "--exit-price", "1102", "--exit-ts", TS, "--apply"])
        self.assertEqual(rc, 0)
        with Journal(self.db) as j:
            self.assertEqual(j.trade_count_today(_ts_dt()), 2)

    def test_duplicate_is_refused(self):
        with Journal(self.db) as j:
            j.record_trade(side="buy", code="068050", qty=74, price=1111.0,
                           ts=_ts_dt(), screener="daytrading")
        rc = backfill_main(["--journal", self.db, "--code", "068050", "--qty", "74",
                            "--price", "1111.1351", "--entry-ts", TS, "--apply"])
        self.assertEqual(rc, 3)


def _ts_dt():
    from datetime import datetime

    from trader_core.config import KST

    return datetime.fromisoformat(TS).astimezone(KST)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
