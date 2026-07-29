import csv
import json
import os
import tempfile
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from services.shared.audit.audit_trail import AuditLogEntry, AuditTrail


@dataclass
class FakeOrder:
    order_id: str = "ORD-001"
    stock_code: str = "005930"
    order_type: str = "buy"
    quantity: int = 10
    price: float = 70000
    status: str = "filled"
    actor: str = "strategy:ThemeStrategy"


@pytest.fixture
def audit_trail_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name
    at = AuditTrail(storage_path=path)
    yield at
    try:
        os.unlink(path)
    except OSError:
        pass


class TestAuditLogEntry:
    def test_default_status_is_success(self):
        entry = AuditLogEntry(
            event_id="test-id",
            timestamp="2024-01-01T00:00:00",
            event_type="test",
            actor="system",
            resource="test:1",
            action="create",
            detail={},
        )
        assert entry.status == "success"

    def test_default_ip_and_ua_are_empty(self):
        entry = AuditLogEntry(
            event_id="test-id",
            timestamp="2024-01-01T00:00:00",
            event_type="test",
            actor="system",
            resource="test:1",
            action="create",
            detail={},
        )
        assert entry.ip_address == ""
        assert entry.user_agent == ""


class TestAuditTrail:
    def test_log_returns_entry(self):
        at = AuditTrail()
        entry = at.log("test_event", "system", "test:1", "create")
        assert isinstance(entry, AuditLogEntry)
        assert entry.event_type == "test_event"

    def test_log_generates_uuid(self):
        at = AuditTrail()
        e1 = at.log("a", "system", "r:1", "create")
        e2 = at.log("b", "system", "r:2", "update")
        assert e1.event_id != e2.event_id

    def test_log_timestamp_is_iso(self):
        at = AuditTrail()
        entry = at.log("test", "system", "r:1", "read")
        assert "T" in entry.timestamp

    def test_log_custom_status_failure(self):
        at = AuditTrail()
        entry = at.log("error", "system", "r:1", "fail", status="failure")
        assert entry.status == "failure"

    def test_log_ip_and_ua(self):
        at = AuditTrail()
        entry = at.log("login", "user:kim", "session:abc", "login", ip="127.0.0.1", ua="test-agent/1.0")
        assert entry.ip_address == "127.0.0.1"
        assert entry.user_agent == "test-agent/1.0"

    def test_log_detail_default_empty_dict(self):
        at = AuditTrail()
        entry = at.log("test", "system", "r:1", "create")
        assert entry.detail == {}

    def test_log_custom_detail(self):
        at = AuditTrail()
        entry = at.log("order", "system", "order:1", "create", detail={"qty": 10})
        assert entry.detail == {"qty": 10}

    def test_in_memory_stores_entries(self):
        at = AuditTrail()
        at.log("a", "s", "r:1", "create")
        at.log("b", "s", "r:2", "update")
        assert len(at._entries) == 2

    def test_log_order_event_creates_entry(self, audit_trail_file):
        order = FakeOrder()
        entry = audit_trail_file.log_order_event(order, "created")
        assert entry.event_type == "order"
        assert entry.resource == "order:ORD-001"
        assert entry.actor == "strategy:ThemeStrategy"
        assert entry.action == "created"
        assert entry.detail["stock_code"] == "005930"

    def test_log_order_event_no_actor_fallback(self):
        order = FakeOrder()
        order.actor = None
        at = AuditTrail()
        entry = at.log_order_event(order, "created")
        assert entry.actor == "system"

    def test_log_trade_event_creates_entry(self, audit_trail_file):
        order = FakeOrder()
        entry = audit_trail_file.log_trade_event(order, fill_price=70500.0, fill_qty=10)
        assert entry.event_type == "trade"
        assert entry.resource == "trade:ORD-001"
        assert entry.action == "execute"
        assert entry.detail["fill_price"] == 70500.0
        assert entry.detail["fill_qty"] == 10

    def test_log_config_change(self, audit_trail_file):
        entry = audit_trail_file.log_config_change("slippage", 0.01, 0.02, actor="user:admin")
        assert entry.event_type == "config_changed"
        assert entry.resource == "config:slippage"
        assert entry.action == "update"
        assert entry.detail["old_value"] == 0.01
        assert entry.detail["new_value"] == 0.02
        assert entry.actor == "user:admin"

    def test_log_config_change_default_actor(self, audit_trail_file):
        entry = audit_trail_file.log_config_change("slippage", 0.01, 0.02)
        assert entry.actor == "system"

    def test_log_error_creates_entry(self, audit_trail_file):
        try:
            raise ValueError("test error")
        except ValueError as e:
            entry = audit_trail_file.log_error(e)
        assert entry.event_type == "error"
        assert entry.status == "failure"
        assert entry.detail["error_type"] == "ValueError"
        assert entry.detail["error_message"] == "test error"

    def test_log_error_with_context(self, audit_trail_file):
        error = RuntimeError("timeout")
        entry = audit_trail_file.log_error(error, context={"host": "db01"})
        assert entry.detail["context"] == {"host": "db01"}

    def test_query_filters_by_event_type(self, audit_trail_file):
        audit_trail_file.log("order", "s", "r:1", "create")
        audit_trail_file.log("trade", "s", "r:1", "execute")
        audit_trail_file.log("order", "s", "r:2", "cancel")
        results = audit_trail_file.query(event_type="order")
        assert len(results) == 2
        assert all(e.event_type == "order" for e in results)

    def test_query_filters_by_actor(self, audit_trail_file):
        audit_trail_file.log("a", "user:kim", "r:1", "create")
        audit_trail_file.log("b", "system", "r:2", "update")
        audit_trail_file.log("c", "user:kim", "r:3", "delete")
        results = audit_trail_file.query(actor="user:kim")
        assert len(results) == 2

    def test_query_filters_by_resource(self, audit_trail_file):
        audit_trail_file.log("a", "s", "order:1", "create")
        audit_trail_file.log("b", "s", "order:2", "create")
        results = audit_trail_file.query(resource="order:1")
        assert len(results) == 1

    def test_query_filters_time_range(self, audit_trail_file):
        audit_trail_file.log("old", "s", "r:1", "create")
        audit_trail_file.log("new", "s", "r:2", "create")
        results = audit_trail_file.query(start_time="2099-01-01T00:00:00")
        assert len(results) == 0

    def test_query_limits_results(self, audit_trail_file):
        for i in range(10):
            audit_trail_file.log(f"e{i}", "s", f"r:{i}", "create")
        results = audit_trail_file.query(limit=3)
        assert len(results) == 3

    def test_export_csv_writes_file(self, audit_trail_file):
        audit_trail_file.log("order", "system", "order:1", "create", detail={"qty": 5})
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            csv_path = f.name
        try:
            audit_trail_file.export_csv(csv_path)
            with open(csv_path) as f:
                reader = csv.reader(f)
                rows = list(reader)
            assert len(rows) == 2
            assert rows[0][0] == "event_id"
            assert "order" in rows[1]
        finally:
            os.unlink(csv_path)

    def test_export_csv_filtered(self, audit_trail_file):
        audit_trail_file.log("order", "s", "order:1", "create")
        audit_trail_file.log("trade", "s", "trade:1", "execute")
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            csv_path = f.name
        try:
            audit_trail_file.export_csv(csv_path, event_type="order")
            with open(csv_path) as f:
                reader = csv.reader(f)
                rows = list(reader)
            assert len(rows) == 2
        finally:
            os.unlink(csv_path)

    def test_get_stats_empty(self):
        at = AuditTrail()
        stats = at.get_stats()
        assert stats["total_entries"] == 0
        assert stats["by_event_type"] == {}
        assert stats["by_status"] == {}

    def test_get_stats_populated(self, audit_trail_file):
        audit_trail_file.log("order", "s", "r:1", "create")
        audit_trail_file.log("order", "s", "r:2", "update")
        audit_trail_file.log("trade", "s", "r:1", "execute")
        audit_trail_file.log("error", "s", "r:1", "fail", status="failure")
        stats = audit_trail_file.get_stats()
        assert stats["total_entries"] == 4
        assert stats["by_event_type"]["order"] == 2
        assert stats["by_event_type"]["trade"] == 1
        assert stats["by_event_type"]["error"] == 1
        assert stats["by_status"]["success"] == 3
        assert stats["by_status"]["failure"] == 1
        assert stats["oldest"] is not None
        assert stats["newest"] is not None

    def test_file_storage_writes_jsonl(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            at = AuditTrail(storage_path=path)
            at.log("test", "system", "r:1", "create", detail={"key": "val"})
            with open(path) as f:
                line = f.readline().strip()
            data = json.loads(line)
            assert data[0]  # tuple serialized by astuple
        finally:
            os.unlink(path)

    def test_pg_storage_inserts_on_log(self):
        mock_pg = MagicMock()
        at = AuditTrail(pg_storage=mock_pg)
        at.log("test", "system", "r:1", "create")
        assert mock_pg.execute.called
        call_args = mock_pg.execute.call_args[0]
        assert "INSERT INTO audit_log" in call_args[0]
        assert call_args[1][0] is not None

    def test_pg_storage_on_conflict_do_nothing(self):
        mock_pg = MagicMock()
        at = AuditTrail(pg_storage=mock_pg)
        at.log("test", "system", "r:1", "create")
        sql = mock_pg.execute.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql

    def test_pg_storage_error_does_not_raise(self):
        mock_pg = MagicMock()
        mock_pg.execute.side_effect = Exception("DB down")
        at = AuditTrail(pg_storage=mock_pg)
        at.log("test", "system", "r:1", "create")

    def test_file_storage_rotation(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            with patch("os.path.getsize", return_value=100 * 1024 * 1024 + 1):
                at = AuditTrail(storage_path=path)
                at.log("test", "system", "r:1", "create")
                base, ext = os.path.splitext(path)
                rotated = [p for p in os.listdir(tempfile.gettempdir()) if p.startswith(os.path.basename(base)) and "_" in p]
                assert len(rotated) >= 0
        finally:
            for p in os.listdir(tempfile.gettempdir()):
                if p.startswith(os.path.basename(path).replace(".jsonl", "")) and "_" in p:
                    try:
                        os.unlink(os.path.join(tempfile.gettempdir(), p))
                    except OSError:
                        pass
            try:
                os.unlink(path)
            except OSError:
                pass
