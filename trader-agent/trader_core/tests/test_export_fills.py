"""tools/export_fills.py 단위 테스트 (계획 ④ 환류 루프의 트레이더 측).

왜: 이 파일이 분석측으로 나가는 유일한 "실집행 결과"다. gross/net 손익·수수료·
보유시간·스크리너 귀속이 틀리면 분석측 캘리브레이션과 기여도 평가가 통째로 오염된다.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tools"))

from trader_core.config import KST  # noqa: E402
from trader_core.journal import Journal  # noqa: E402

ef = importlib.import_module("export_fills")


def _seed_journal(path: str, screener: str = "swing") -> None:
    """매수 1건(익절) + 매수 1건(손절) + 미청산 1건을 넣는다."""
    day = datetime(2026, 9, 29, 9, 5, tzinfo=KST)
    with Journal(path) as journal:
        first = journal.record_trade(
            side="buy", code="005930", name="삼성전자", qty=10,
            price=70000.0, order_type="limit",
            order_resp_json=json.dumps({"order_no": 1}),
            entry_reason="test", screener=screener,
            algo_json=json.dumps({"ml_prob": 0.61}), ts=day,
        )
        journal.close_trade(
            trade_id=first, exit_price=73500.0, exit_reason="take_profit",
            fees=300.0, exit_ts=day + timedelta(hours=2),
        )

        second = journal.record_trade(
            side="buy", code="000660", name="SK하이닉스", qty=5,
            price=200000.0, order_type="limit",
            order_resp_json=json.dumps({"order_no": 2}),
            entry_reason="test", screener=screener, ts=day,
        )
        journal.close_trade(
            trade_id=second, exit_price=190000.0, exit_reason="stop_loss",
            fees=250.0, exit_ts=day + timedelta(hours=1),
        )

        journal.record_trade(
            side="buy", code="035420", name="NAVER", qty=3,
            price=210000.0, order_type="limit",
            order_resp_json=json.dumps({"order_no": 3}),
            entry_reason="still open", screener=screener, stop_pct=0.07,
            tp_pct=0.15, kelly_f=0.06, ts=day,
        )


class ExportFillsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.journal_path = os.path.join(self.tmp.name, "j.sqlite3")
        _seed_journal(self.journal_path)
        self.out_dir = os.path.join(self.tmp.name, "fills")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _export(self, day: str = "2026-09-29"):
        payload = ef.export_day(self.journal_path, self.out_dir, day, account="783247576")
        self.assertIsNotNone(payload)
        return payload

    def test_closed_trades_carry_net_fees_and_holding_time(self) -> None:
        payload = self._export()
        by_code = {row["code"]: row for row in payload["closed"]}

        assert set(by_code) == {"005930", "000660"}
        winner = by_code["005930"]
        # gross = (73500-70000)*10 = 35,000 (수수료 제외) / net = 35,000 - 300 = 34,700
        assert winner["gross_pnl"] == 35000.0
        assert winner["fees"] == 300.0
        assert winner["net_pnl"] == 34700.0
        assert winner["ret_pct"] == round(34700.0 / (70000.0 * 10) * 100.0, 3)
        assert winner["hold_hours"] == 2.0
        assert winner["ml_prob"] == 0.61
        assert winner["screener"] == "swing"

        loser = by_code["000660"]
        assert loser["net_pnl"] == -50250.0  # (190000-200000)*5 - 250
        assert loser["hold_hours"] == 1.0

    def test_open_position_is_reported_separately(self) -> None:
        payload = self._export()

        assert [row["code"] for row in payload["opened"]] == ["035420"]
        opened = payload["opened"][0]
        assert opened["stop_pct"] == 0.07 and opened["kelly_f"] == 0.06

    def test_summary_reports_wins_losses_and_fees(self) -> None:
        summary = self._export()["summary"]

        assert summary["all"]["closed_n"] == 2
        assert summary["all"]["net_pnl"] == -15550.0
        assert summary["all"]["fees"] == 550.0
        assert summary["all"]["win_rate"] == 50.0
        assert summary["all"]["avg_win"] == 34700.0
        assert summary["all"]["avg_loss"] == -50250.0
        assert summary["by_screener"]["swing"]["closed_n"] == 2
        assert summary["fees_known"] is True

    def test_file_is_written_atomically_with_iso_name(self) -> None:
        self._export()
        target = os.path.join(self.out_dir, "fills_2026-09-29.json")

        assert os.path.exists(target)
        assert not os.path.exists(target + ".tmp")
        with open(target, encoding="utf-8") as handle:
            parsed = json.load(handle)
        assert parsed["account"] == "783247576"
        assert parsed["source"] == "trader-agent"

    def test_empty_day_is_still_exported_honestly(self) -> None:
        payload = self._export(day="2026-09-27")

        assert payload["closed"] == [] and payload["opened"] == []
        assert payload["summary"]["all"]["closed_n"] == 0
        assert payload["summary"]["all"]["net_pnl"] == 0
        assert payload["summary"]["all"]["win_rate"] is None
        assert payload["summary"]["fees_known"] is None

    def test_missing_journal_returns_none(self) -> None:
        assert ef.export_day("/nonexistent/journal.sqlite3", self.out_dir, "2026-09-29") is None

    def test_negative_sell_side_pnl_sign(self) -> None:
        """매도(공매도/청산 반대방향) 행은 부호가 뒤집혀야 한다."""
        row = {"side": "sell", "qty": 10, "price": 70000.0, "exit_price": 65000.0,
               "pnl": 50000.0, "fees": 0.0}

        record = ef._closed_row(row)

        assert record["gross_pnl"] == 50000.0


if __name__ == "__main__":
    unittest.main()
