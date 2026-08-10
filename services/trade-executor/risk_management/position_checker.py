"""
Position Checker
Validates position limits and updates position data in PostgreSQL.
"""

from typing import Dict, Optional, List
from loguru import logger
from config import Config


class PositionChecker:
    """Checks and manages trading positions."""

    def __init__(self):
        self.config = Config()
        self._positions: Dict[str, Dict] = {}
        self._last_sync = None

    def check_position_limit(self, stock_code: str, additional_quantity: int,
                             order_price: Optional[float] = None) -> bool:
        """
        Check if adding a position would exceed limits.

        Args:
            stock_code: Stock code
            additional_quantity: Number of shares to add
            order_price: Current order price (CWE-862 방어 — avg_price=0/지연 시
                집중도 계산이 0이 되어 20% 상한을 우회하는 경로 차단)

        Returns:
            True if position is within limits
        """
        # Sync positions from DB
        self._sync_positions()

        current_position = self._positions.get(stock_code, {})
        current_qty = current_position.get("quantity", 0)
        current_value = current_position.get("current_value", 0)

        # Check if already holding maximum positions
        max_positions = 20  # Maximum distinct stock positions
        if len(self._positions) >= max_positions and stock_code not in self._positions:
            logger.warning(f"Max positions ({max_positions}) reached")
            return False

        # Check position concentration (no single stock > 20% of portfolio)
        total_portfolio_value = self._get_total_portfolio_value()
        if total_portfolio_value > 0:
            # 신규/avg_price=0 포지션도 order_price 기준으로 가치 산정 (CWE-862)
            price_for_value = (order_price or 0) if order_price else current_position.get("avg_price", 0)
            new_value = current_value + (additional_quantity * price_for_value)
            concentration = new_value / total_portfolio_value
            if concentration > 0.20:
                logger.warning(f"Position concentration {concentration:.2%} exceeds 20%")
                return False

        return True

    def update_position(self, stock_code: str, action: str, quantity: int,
                        price: Optional[float] = None):
        """Update local position after trade execution."""
        if stock_code not in self._positions:
            self._positions[stock_code] = {
                "stock_code": stock_code,
                "quantity": 0,
                "avg_price": 0,
                "current_value": 0,
            }

        pos = self._positions[stock_code]
        if action == "buy":
            total_qty = pos["quantity"] + quantity
            total_cost = (pos["avg_price"] * pos["quantity"]) + (price or 0) * quantity
            pos["avg_price"] = total_cost / total_qty if total_qty > 0 else 0
            pos["quantity"] = total_qty
        elif action == "sell":
            pos["quantity"] = max(0, pos["quantity"] - quantity)
            if pos["quantity"] == 0:
                pos["avg_price"] = 0

        pos["current_value"] = pos["quantity"] * (price or pos["avg_price"])
        logger.info(f"Position updated: {stock_code} -> {pos['quantity']} shares")
        self._persist_position(stock_code, pos)

    def get_all_positions(self) -> List[Dict]:
        """Get all current positions."""
        self._sync_positions()
        return [
            {
                "stock_code": code,
                "quantity": info["quantity"],
                "avg_price": info["avg_price"],
                "current_value": info["current_value"],
            }
            for code, info in self._positions.items()
            if info["quantity"] > 0
        ]

    def get_position(self, stock_code: str) -> Optional[Dict]:
        """Get position for a specific stock."""
        self._sync_positions()
        return self._positions.get(stock_code)

    def _sync_positions(self):
        """Sync positions from PostgreSQL database."""
        try:
            import psycopg2

            conn = psycopg2.connect(
                host=self.config.POSTGRES_HOST,
                port=self.config.POSTGRES_PORT,
                dbname=self.config.POSTGRES_DB,
                user=self.config.POSTGRES_USER,
                password=self.config.POSTGRES_PASSWORD,
            )
            cur = conn.cursor()
            cur.execute("""
                SELECT stock_code, quantity, avg_buy_price, current_price
                FROM positions
                WHERE quantity > 0
            """)
            for row in cur.fetchall():
                stock_code, qty, avg_price, current_price = row
                self._positions[stock_code] = {
                    "stock_code": stock_code,
                    "quantity": qty or 0,
                    "avg_price": float(avg_price or 0),
                    "current_value": (qty or 0) * float(current_price or 0),
                }
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to sync positions: {e}")

    def _persist_position(self, stock_code: str, pos: Dict):
        """Save/update position to PostgreSQL."""
        try:
            import psycopg2

            conn = psycopg2.connect(
                host=self.config.POSTGRES_HOST,
                port=self.config.POSTGRES_PORT,
                dbname=self.config.POSTGRES_DB,
                user=self.config.POSTGRES_USER,
                password=self.config.POSTGRES_PASSWORD,
            )
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO positions (stock_code, quantity, avg_buy_price, current_price, updated_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (stock_code) DO UPDATE SET
                    quantity = EXCLUDED.quantity,
                    avg_buy_price = EXCLUDED.avg_buy_price,
                    current_price = EXCLUDED.current_price,
                    updated_at = CURRENT_TIMESTAMP
            """, (stock_code, pos["quantity"], pos["avg_price"], pos["current_value"]))
            conn.commit()
            cur.close()
            conn.close()
            logger.debug(f"Position persisted to PG: {stock_code}")
        except Exception as e:
            logger.warning(f"Failed to persist position to PG: {e}")

    def _get_total_portfolio_value(self) -> float:
        """Calculate total portfolio value."""
        return sum(
            p.get("current_value", 0)
            for p in self._positions.values()
            if p.get("quantity", 0) > 0
        )
