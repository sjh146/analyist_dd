import pytest
from executors.order_lifecycle import Order, OrderLifecycleManager


class TestOrder:
    def test_create_order_defaults(self):
        order = Order()
        assert order.status == 'CREATED'
        assert order.order_style == 'market'
        assert order.order_id is not None
        assert order.created_at != ''

    def test_custom_order(self):
        order = Order(
            stock_code='005930',
            order_type='buy',
            quantity=10,
            price=70000,
        )
        assert order.stock_code == '005930'
        assert order.order_type == 'buy'
        assert order.quantity == 10
        assert order.price == 70000.0


class TestOrderLifecycleManager:
    def test_create_order(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10, price=70000)
        assert order.status == 'CREATED'
        assert order.order_id in mgr._orders

    def test_full_lifecycle_sell(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'sell', 5, price=71000)
        order = mgr.validate_order(order)
        assert order.status == 'VALIDATED'
        order = mgr.route_order(order)
        assert order.status == 'ROUTED'
        order = mgr.submit_order(order)
        assert order.status == 'SUBMITTED'
        order = mgr.update_status(
            order.order_id, 'FILLED', filled_qty=5, fill_price=71000
        )
        assert order.status == 'FILLED'
        assert order.filled_quantity == 5
        assert order.avg_fill_price == 71000

    def test_partial_fill_then_full(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('000660', 'buy', 100, price=50000)
        order = mgr.validate_order(order)
        order = mgr.route_order(order)
        order = mgr.submit_order(order)
        order = mgr.update_status(
            order.order_id, 'PARTIAL', filled_qty=40, fill_price=50100
        )
        assert order.status == 'PARTIAL'
        assert order.filled_quantity == 40
        assert order.avg_fill_price == 50100
        order = mgr.update_status(
            order.order_id, 'FILLED', filled_qty=100, fill_price=50200
        )
        assert order.status == 'FILLED'
        assert order.filled_quantity == 100
        assert order.avg_fill_price == 50200

    def test_cancel_pending_order(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('035420', 'buy', 20)
        result = mgr.cancel_order(order.order_id)
        assert result.status == 'CANCELLED'

    def test_cancel_filled_order_fails(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        order = mgr.validate_order(order)
        order = mgr.route_order(order)
        order = mgr.submit_order(order)
        order = mgr.update_status(order.order_id, 'FILLED')
        result = mgr.cancel_order(order.order_id)
        assert result.status == 'FILLED'
        assert 'Cannot cancel' in result.error

    def test_invalid_create_to_filled(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        result = mgr.update_status(order.order_id, 'FILLED')
        assert result.status == 'CREATED'
        assert result.error != ''

    def test_validate_rejects_after_validation(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        order = mgr.validate_order(order)
        result = mgr.validate_order(order)
        assert result.status == 'REJECTED'
        assert 'Cannot validate from state VALIDATED' in result.error

    def test_route_rejects_non_validated(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        result = mgr.route_order(order)
        assert result.status == 'REJECTED'

    def test_submit_rejects_non_routed(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        order = mgr.validate_order(order)
        result = mgr.submit_order(order)
        assert result.status == 'REJECTED'

    def test_validate_with_balance_checker_passes(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10, price=70000)

        def check_balance(o):
            return True

        result = mgr.validate_order(order, balance_checker=check_balance)
        assert result.status == 'VALIDATED'

    def test_validate_with_balance_checker_fails(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10, price=70000)

        def check_balance(o):
            return False

        result = mgr.validate_order(order, balance_checker=check_balance)
        assert result.status == 'REJECTED'
        assert 'Balance check failed' in result.error

    def test_validate_with_broken_checker(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)

        def broken_checker(o):
            raise RuntimeError('DB down')

        result = mgr.validate_order(order, balance_checker=broken_checker)
        assert result.status == 'REJECTED'
        assert 'DB down' in result.error

    def test_route_with_router(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        order = mgr.validate_order(order)
        routed = []
        route_result = mgr.route_order(
            order, router=lambda o: routed.append(o.order_id)
        )
        assert route_result.status == 'ROUTED'
        assert len(routed) == 1

    def test_get_order_nonexistent(self):
        mgr = OrderLifecycleManager()
        assert mgr.get_order('does-not-exist') is None

    def test_get_order_exists(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        assert mgr.get_order(order.order_id) is order

    def test_get_pending_orders(self):
        mgr = OrderLifecycleManager()
        o1 = mgr.create_order('005930', 'buy', 10)
        o2 = mgr.create_order('000660', 'sell', 5)
        o3 = mgr.create_order('035420', 'buy', 20)
        mgr.cancel_order(o3.order_id)
        pending = mgr.get_pending_orders()
        assert len(pending) == 2
        for o in pending:
            assert o.status in ('CREATED',)

    def test_get_pending_orders_after_fill(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        order = mgr.validate_order(order)
        order = mgr.route_order(order)
        order = mgr.submit_order(order)
        order = mgr.update_status(order.order_id, 'FILLED')
        pending = mgr.get_pending_orders()
        assert len(pending) == 0

    def test_get_order_history_all(self):
        mgr = OrderLifecycleManager()
        mgr.create_order('005930', 'buy', 10, strategy='theme')
        mgr.create_order('000660', 'sell', 5, strategy='cycle')
        mgr.create_order('035420', 'buy', 20, strategy='theme')
        assert len(mgr.get_order_history()) == 3

    def test_get_order_history_by_strategy(self):
        mgr = OrderLifecycleManager()
        mgr.create_order('005930', 'buy', 10, strategy='theme')
        mgr.create_order('000660', 'sell', 5, strategy='cycle')
        mgr.create_order('035420', 'buy', 20, strategy='theme')
        theme_orders = mgr.get_order_history(strategy='theme')
        assert len(theme_orders) == 2

    def test_get_order_history_by_status(self):
        mgr = OrderLifecycleManager()
        o1 = mgr.create_order('005930', 'buy', 10)
        o2 = mgr.create_order('000660', 'sell', 5)
        mgr.cancel_order(o2.order_id)
        cancelled = mgr.get_order_history(status='CANCELLED')
        assert len(cancelled) == 1
        assert cancelled[0].order_id == o2.order_id

    def test_get_order_history_limit(self):
        mgr = OrderLifecycleManager()
        for i in range(5):
            mgr.create_order('005930', 'buy', 10)
        assert len(mgr.get_order_history(limit=3)) == 3

    def test_get_order_history_recent_first(self):
        mgr = OrderLifecycleManager()
        o1 = mgr.create_order('005930', 'buy', 10)
        o2 = mgr.create_order('000660', 'sell', 5)
        recent = mgr.get_order_history(limit=2)
        assert recent[0].order_id == o2.order_id
        assert recent[1].order_id == o1.order_id

    def test_update_status_nonexistent(self):
        mgr = OrderLifecycleManager()
        assert mgr.update_status('no-such-id', 'FILLED') is None

    def test_cancel_nonexistent(self):
        mgr = OrderLifecycleManager()
        assert mgr.cancel_order('no-such-id') is None

    def test_create_with_kwargs(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order(
            '005930',
            'buy',
            10,
            price=70000,
            strategy='twin',
            reason='z-score divergence',
            order_style='limit',
        )
        assert order.strategy_name == 'twin'
        assert order.reason == 'z-score divergence'
        assert order.order_style == 'limit'

    def test_submit_with_executor(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        order = mgr.validate_order(order)
        order = mgr.route_order(order)
        exec_called = False

        def fake_executor(o):
            nonlocal exec_called
            exec_called = True

        result = mgr.submit_order(order, executor=fake_executor)
        assert result.status == 'SUBMITTED'
        assert exec_called

    def test_submit_with_failing_executor(self):
        mgr = OrderLifecycleManager()
        order = mgr.create_order('005930', 'buy', 10)
        order = mgr.validate_order(order)
        order = mgr.route_order(order)

        def failing_executor(o):
            raise RuntimeError('API unavailable')

        result = mgr.submit_order(order, executor=failing_executor)
        assert result.status == 'REJECTED'
        assert 'API unavailable' in result.error
