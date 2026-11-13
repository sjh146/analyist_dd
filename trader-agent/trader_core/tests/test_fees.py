"""수수료·세금 파생 테스트 (백로그 T1).

기준은 실측이다: 2026-09-28 삼현(437730)에서 브로커 매도실현손익 -6,516원 vs 저널 gross -6,400원
→ 비용 약 116원(회전 6.9만원의 0.17%). 즉 **브로커 값이 net, 저널 값이 gross** 라는 가정이
이 테스트에 고정되어 있다.  이 가정이 실계좌 첫 청산에서 깨지면(차이 0) 테스트가 아니라
접근 자체를 바꿔야 한다 — ``trader_core/fees.py`` 모듈 docstring 참조.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from trader_core import Journal
from trader_core.fees import derive_fees, journal_row_from_trade, normalise_code, same_day_guard

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=KST)


class DeriveFeesTest(unittest.TestCase):
    def test_real_measurement_sign_and_size(self) -> None:
        """삼현 실측: 저널 gross -6,400 · 브로커 net -6,516 → 비용 +116원(부호 검산)."""
        rows = [{"id": 1, "code": "437730", "qty": 27, "entry": 2800.0, "exit_px": 2563.0}]
        items = [{"code": "437730", "sell_qty": 27, "realized_pnl": -6516.0}]
        allocations, unmatched_j, unmatched_b = derive_fees(rows, items)
        self.assertEqual(len(allocations), 1)
        alloc = allocations[0]
        self.assertAlmostEqual(float(alloc["gross"]), -6399.0)
        self.assertAlmostEqual(float(alloc["fee"]), 117.0)  # gross - net = 117 (양수=비용)
        self.assertEqual(alloc["flag"], "ok")
        self.assertEqual(unmatched_j, [])
        self.assertEqual(unmatched_b, [])
        # 배분된 행 합계 = 종목 비용
        self.assertAlmostEqual(sum(r["fee"] for r in alloc["rows"]), float(alloc["fee"]))

    def test_profitable_exit_still_charges_cost(self) -> None:
        """이익 청산에서도 비용은 양수여야 한다(부호가 뒤집히면 net 이 부풀려진다)."""
        rows = [{"id": 7, "code": "005930", "qty": 10, "entry": 10000.0, "exit_px": 11000.0}]
        items = [{"code": "005930", "sell_qty": 10, "realized_pnl": 9600.0}]
        allocations, _, _ = derive_fees(rows, items)
        self.assertAlmostEqual(float(allocations[0]["fee"]), 400.0)  # 10,000 gross - 9,600 net
        self.assertEqual(allocations[0]["flag"], "ok")

    def test_multiple_closings_same_code_are_allocated_by_gross(self) -> None:
        rows = [
            {"id": 1, "code": "A005930", "qty": 3, "entry": 1000.0, "exit_px": 1100.0},  # +300
            {"id": 2, "code": "005930", "qty": 3, "entry": 1000.0, "exit_px": 1200.0},   # +600
        ]
        items = [{"code": "005930", "sell_qty": 6, "realized_pnl": 0.0}]  # 900 gross - 900 비용
        allocations, _, _ = derive_fees(rows, items)
        self.assertEqual(len(allocations), 1)
        fee = float(allocations[0]["fee"])
        self.assertAlmostEqual(fee, 900.0)
        shares = {r["id"]: r["fee"] for r in allocations[0]["rows"]}
        self.assertAlmostEqual(shares[1], 300.0)   # 1/3
        self.assertAlmostEqual(shares[2], 600.0)   # 2/3
        self.assertAlmostEqual(sum(shares.values()), fee)  # 잔차 흡수

    def test_unmatched_both_ways_are_reported_not_guessed(self) -> None:
        rows = [{"id": 5, "code": "111111", "qty": 1, "entry": 1000.0, "exit_px": 1100.0}]
        items = [{"code": "222222", "sell_qty": 1, "realized_pnl": -50.0}]
        allocations, unmatched_j, unmatched_b = derive_fees(rows, items)
        self.assertEqual(allocations, [])
        self.assertEqual(len(unmatched_j), 1)
        self.assertEqual(unmatched_j[0]["code"], "111111")
        self.assertEqual(len(unmatched_b), 1)
        self.assertEqual(unmatched_b[0]["code"], "222222")

    def test_suspicious_cost_is_flagged(self) -> None:
        """음수 비용(=브로커가 우리 gross 보다 좋다)은 조용히 넘기지 않고 표시한다."""
        rows = [{"id": 9, "code": "005930", "qty": 1, "entry": 1000.0, "exit_px": 1000.0}]
        items = [{"code": "005930", "sell_qty": 1, "realized_pnl": 100.0}]
        allocations, _, _ = derive_fees(rows, items)
        self.assertTrue(str(allocations[0]["flag"]).startswith("check"))

    def test_helpers(self) -> None:
        self.assertEqual(normalise_code(" a005930 "), "005930")
        self.assertIsNone(journal_row_from_trade({"id": 1, "exit_ts": None}))
        self.assertIsNotNone(
            journal_row_from_trade({"id": 1, "code": "005930", "qty": 1,
                                    "price": 1000.0, "exit_ts": "x", "exit_price": 1100.0})
        )


class SetFeesTest(unittest.TestCase):
    def _journal_with_closed_trade(self) -> tuple[Journal, int]:
        journal = Journal(":memory:")
        trade_id = journal.record_trade(
            side="buy", code="437730", name="삼현", qty=27, price=2800.0,
            order_type="limit", entry_reason="signal", stop_pct=0.07, tp_pct=0.15, ts=NOW,
        )
        journal.close_trade(trade_id, exit_price=2563.0, exit_reason="stop",
                            fees=0.0, exit_ts=NOW + timedelta(hours=2))
        return journal, trade_id

    def test_set_fees_writes_net_and_is_idempotent(self) -> None:
        journal, trade_id = self._journal_with_closed_trade()
        self.assertTrue(journal.set_fees(trade_id, 117.0))
        row = journal.closed_on("2026-09-28")[0]
        self.assertAlmostEqual(float(row["fees"]), 117.0)
        self.assertAlmostEqual(float(row["pnl"]), (2563.0 - 2800.0) * 27 - 117.0)
        # 두 번 호출해도 같은 값(가격에서 재계산하므로 누적 차감되지 않는다)
        self.assertTrue(journal.set_fees(trade_id, 117.0))
        row2 = journal.closed_on("2026-09-28")[0]
        self.assertAlmostEqual(float(row2["pnl"]), float(row["pnl"]))

    def test_closed_on_filters_by_day(self) -> None:
        journal, _ = self._journal_with_closed_trade()
        self.assertEqual(len(journal.closed_on("2026-09-28")), 1)
        self.assertEqual(len(journal.closed_on("2026-09-27")), 0)

    def test_set_fees_unknown_or_open_row_is_false(self) -> None:
        journal = Journal(":memory:")
        open_id = journal.record_trade(side="buy", code="005930", qty=1, price=1000.0, ts=NOW)
        self.assertFalse(journal.set_fees(open_id, 10.0))
        self.assertFalse(journal.set_fees(999999, 10.0))


class SameDayGuardTest(unittest.TestCase):
    """브로커 손익 조회(CpTd6032)는 당일만 제공한다 — 과거 요청은 조용히 0건이 되면 안 된다.

    실측 2026-10-04: 09-30 청산 3건의 수수료를 채우려고 ``--date 2026-09-30`` 로 돌렸더니
    '브로커 매도 종목 0건' 이 나왔다(당일 조회라서). 그대로 두면 "비용 0" 으로 오독되므로
    과거 날짜는 명시적으로 거부한다. 계상은 청산 당일 16:05 자동 틱이 담당한다.
    """

    def test_same_day_passes(self) -> None:
        self.assertIsNone(same_day_guard("2026-10-06", "2026-10-06"))

    def test_past_day_is_refused_with_reason(self) -> None:
        msg = same_day_guard("2026-09-30", "2026-10-04")
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("당일", msg)
        self.assertIn("2026-09-30", msg)

    def test_future_day_is_refused(self) -> None:
        self.assertIsNotNone(same_day_guard("2026-10-07", "2026-10-04"))


if __name__ == "__main__":
    unittest.main()
