from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional


VALID_TRANSITIONS = {
    'CREATED': {'VALIDATED', 'CANCELLED', 'REJECTED'},
    'VALIDATED': {'ROUTED', 'CANCELLED', 'REJECTED'},
    'ROUTED': {'SUBMITTED', 'CANCELLED', 'REJECTED'},
    'SUBMITTED': {'PARTIAL', 'FILLED', 'CANCELLED', 'REJECTED'},
    'PARTIAL': {'FILLED', 'CANCELLED', 'REJECTED'},
    'FILLED': set(),
    'CANCELLED': set(),
    'REJECTED': set(),
}


@dataclass
class Order:
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    stock_code: str = ''
    order_type: str = ''
    order_style: str = 'market'
    quantity: int = 0
    price: float = 0.0
    status: str = 'CREATED'
    strategy_name: str = ''
    reason: str = ''
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    filled_quantity: int = 0
    avg_fill_price: float = 0.0
    error: str = ''


class OrderLifecycleManager:
    def __init__(self, db_connector: Optional[Callable] = None):
        self._orders: Dict[str, Order] = {}
        self._db = db_connector

    def create_order(
        self,
        stock_code: str,
        order_type: str,
        quantity: int,
        price: float = 0.0,
        strategy: str = '',
        **kwargs,
    ) -> Order:
        order = Order(
            stock_code=stock_code,
            order_type=order_type,
            order_style=kwargs.get('order_style', 'market'),
            quantity=quantity,
            price=price,
            strategy_name=strategy,
            reason=kwargs.get('reason', ''),
            status='CREATED',
        )
        self._orders[order.order_id] = order
        self._persist(order)
        return order

    def validate_order(
        self, order: Order, balance_checker: Optional[Callable] = None
    ) -> Order:
        if order.status != 'CREATED':
            return self._reject(order, f'Cannot validate from state {order.status}')
        if balance_checker:
            try:
                ok = balance_checker(order)
                if not ok:
                    return self._reject(order, 'Balance check failed')
            except Exception as e:
                return self._reject(order, f'Balance check error: {e}')
        return self._transition(order, 'VALIDATED')

    def route_order(
        self, order: Order, router: Optional[Callable] = None
    ) -> Order:
        if order.status != 'VALIDATED':
            return self._reject(order, f'Cannot route from state {order.status}')
        if router:
            try:
                router(order)
            except Exception as e:
                return self._reject(order, f'Route error: {e}')
        return self._transition(order, 'ROUTED')

    def submit_order(
        self, order: Order, executor: Optional[Callable] = None
    ) -> Order:
        if order.status not in ('ROUTED',):
            return self._reject(order, f'Cannot submit from state {order.status}')
        if executor:
            try:
                executor(order)
            except Exception as e:
                return self._reject(order, f'Execution error: {e}')
        return self._transition(order, 'SUBMITTED')

    def update_status(
        self,
        order_id: str,
        new_status: str,
        filled_qty: int = 0,
        fill_price: float = 0.0,
    ) -> Optional[Order]:
        order = self._orders.get(order_id)
        if not order:
            return None
        if new_status not in VALID_TRANSITIONS.get(order.status, set()):
            order.error = f'Invalid transition: {order.status} -> {new_status}'
            return order
        if filled_qty:
            order.filled_quantity = filled_qty
        if fill_price:
            order.avg_fill_price = fill_price
        return self._transition(order, new_status)

    def cancel_order(self, order_id: str) -> Optional[Order]:
        order = self._orders.get(order_id)
        if not order:
            return None
        if order.status == 'FILLED':
            order.error = 'Cannot cancel filled order'
            return order
        return self._transition(order, 'CANCELLED')

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def get_pending_orders(self) -> List[Order]:
        return [
            o
            for o in self._orders.values()
            if o.status
            in ('CREATED', 'VALIDATED', 'ROUTED', 'SUBMITTED', 'PARTIAL')
        ]

    def get_order_history(
        self,
        strategy: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Order]:
        result = list(self._orders.values())
        if strategy:
            result = [o for o in result if o.strategy_name == strategy]
        if status:
            result = [o for o in result if o.status == status]
        result.sort(key=lambda o: o.created_at, reverse=True)
        return result[:limit]

    def _transition(self, order: Order, new_status: str) -> Order:
        order.status = new_status
        self._persist(order)
        return order

    def _reject(self, order: Order, reason: str) -> Order:
        order.error = reason
        return self._transition(order, 'REJECTED')

    def _persist(self, order: Order):
        if self._db:
            try:
                self._db(order)
            except Exception:
                pass
