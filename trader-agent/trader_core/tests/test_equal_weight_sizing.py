"""예수금 100% 균등배분(equal-weight) 사이징 테스트.

배경(2026-11-09 사용자 지시): "최대 3종목을 매매할 때 예수금 100%로 매매".
이전 구성은 Kelly f*·위험예산이 종목당 ~6-7% 를 강제해 3종목 합계가 30%
예산에도 못 미쳤다.  지금은 남은 예산을 남은 슬롯 수로 나눠 배분한다.
"""

from __future__ import annotations

import unittest

from trader_core import AccountState, Config, Journal, MockBridge
from trader_core.engine import TraderEngine


def _engine(config: Config) -> TraderEngine:
    return TraderEngine(MockBridge(start_cash=0), feed_client=None,
                        config=config, journal=Journal(":memory:"))


class EqualWeightSizingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine(Config())
        self.cfg = self.engine.config

    def test_first_entry_takes_a_third_of_the_deposit(self) -> None:
        account = AccountState(cash=3_000_000, equity=3_000_000)
        qty, invest = self.engine._equal_weight_qty(
            price=10_000, account=account, slots_used=0
        )
        self.assertEqual(qty, 100)          # 1,000,000원 = 예수금의 1/3
        self.assertAlmostEqual(invest, 1_000_000)

    def test_second_entry_divides_the_rest_by_the_remaining_slots(self) -> None:
        account = AccountState(
            cash=2_000_000,
            equity=3_000_000,
            positions=[{"code": "005930", "qty": 100, "avg_price": 10_000}],
        )
        qty, invest = self.engine._equal_weight_qty(
            price=10_000, account=account, slots_used=1
        )
        self.assertEqual(qty, 100)
        self.assertAlmostEqual(invest, 1_000_000)

    def test_three_entries_deploy_the_whole_deposit(self) -> None:
        """3칸을 순차로 채우면 투입 합계가 예수금(3,000,000)에 도달한다."""
        equity = 3_000_000
        cash = equity
        positions = []
        invested_total = 0.0
        for i in range(3):
            account = AccountState(cash=cash, equity=equity, positions=list(positions))
            qty, invest = self.engine._equal_weight_qty(
                price=10_000, account=account,
                batch_invested=0.0, slots_used=len(positions),
            )
            self.assertGreater(qty, 0)
            invested_total += invest
            cash -= invest
            positions.append({"code": f"00000{i}", "qty": qty, "avg_price": 10_000})
        self.assertAlmostEqual(invested_total, equity, delta=10_000)

    def test_budget_cap_stops_the_fourth_slot(self) -> None:
        """전역 3칸이 가득 차면 새 슬롯의 목표는 0 이 된다(예산 100% 소진)."""
        positions = [
            {"code": "000001", "qty": 100, "avg_price": 10_000},
            {"code": "000002", "qty": 100, "avg_price": 10_000},
            {"code": "000003", "qty": 100, "avg_price": 10_000},
        ]
        account = AccountState(cash=0, equity=3_000_000, positions=positions)
        qty, invest = self.engine._equal_weight_qty(
            price=10_000, account=account, slots_used=3
        )
        self.assertEqual(qty, 0)
        self.assertEqual(invest, 0.0)

    def test_available_cash_derivation_when_bridge_omits_cash(self) -> None:
        """브리지가 cash 를 못 줄 때 equity - invested 로 유도한다."""
        account = AccountState(
            cash=0.0,
            cash_known=False,
            equity=500_000,
            positions=[{"code": "000001", "qty": 20, "avg_price": 10_000}],
        )
        self.assertAlmostEqual(self.engine._available_cash(account), 300_000)

    def test_available_cash_zero_when_fully_invested(self) -> None:
        account = AccountState(
            cash=0.0,
            cash_known=False,
            equity=200_000,
            positions=[{"code": "000001", "qty": 20, "avg_price": 10_000}],
        )
        self.assertEqual(self.engine._available_cash(account), 0.0)

    def test_kelly_mode_is_still_selectable(self) -> None:
        """sizing_mode="kelly" 로 되돌리면 기존 보수적 사이징(종목당 ~6%)을 쓴다."""
        from trader_core import risk

        engine = _engine(Config(sizing_mode="kelly"))
        self.assertEqual(engine.config.sizing_mode, "kelly")
        qty, _invest = risk.sized_qty(
            equity=3_000_000,
            price=10_000,
            win_rate=0.53,
            avg_win=1.0,
            avg_loss=1.0,
            kelly_fraction=engine.config.kelly_fraction,
            kelly_cap=engine.config.kelly_cap,
            max_position_pct=engine.config.max_position_pct,
        )
        # f* = 0.53 - 0.47 = 0.06 → 180,000원 → 18주 (예수금의 6%)
        self.assertEqual(qty, 18)


if __name__ == "__main__":
    unittest.main()
