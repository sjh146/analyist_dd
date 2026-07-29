import csv
import json
import logging
import os
import uuid
from dataclasses import astuple, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Optional

from services.shared.audit.logging_config import get_audit_logger

logger = logging.getLogger(__name__)


@dataclass
class AuditLogEntry:
    event_id: str
    timestamp: str
    event_type: str
    actor: str
    resource: str
    action: str
    detail: dict
    ip_address: str = ""
    user_agent: str = ""
    status: str = "success"


_AUDIT_LOG_ENTRY_FIELDS = [f.name for f in fields(AuditLogEntry)]


class AuditTrail:
    def __init__(
        self,
        storage_path: Optional[str] = None,
        pg_storage: Optional[Any] = None,
    ):
        self._storage_path = storage_path
        self._pg_storage = pg_storage
        self._entries: list[AuditLogEntry] = []
        if storage_path:
            os.makedirs(os.path.dirname(storage_path) or ".", exist_ok=True)
            self._rotate_if_needed()

    def log(
        self,
        event_type: str,
        actor: str,
        resource: str,
        action: str,
        detail: Optional[dict] = None,
        status: str = "success",
        ip: str = "",
        ua: str = "",
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            actor=actor,
            resource=resource,
            action=action,
            detail=detail or {},
            ip_address=ip,
            user_agent=ua,
            status=status,
        )
        self._entries.append(entry)
        self._save(entry)
        return entry

    def log_order_event(
        self, order: Any, action: str, detail: Optional[dict] = None
    ) -> AuditLogEntry:
        order_id = getattr(order, "order_id", str(order))
        d = detail or {}
        for attr in ("stock_code", "order_type", "quantity", "price", "status"):
            val = getattr(order, attr, None)
            if val is not None:
                d[attr] = val
        actor = getattr(order, "actor", None) or "system"
        return self.log(
            event_type="order",
            actor=actor,
            resource=f"order:{order_id}",
            action=action,
            detail=d,
        )

    def log_trade_event(
        self,
        order: Any,
        fill_price: float,
        fill_qty: float,
        detail: Optional[dict] = None,
    ) -> AuditLogEntry:
        order_id = getattr(order, "order_id", str(order))
        d = {"fill_price": fill_price, "fill_qty": fill_qty}
        if detail:
            d.update(detail)
        return self.log(
            event_type="trade",
            actor="system",
            resource=f"trade:{order_id}",
            action="execute",
            detail=d,
        )

    def log_config_change(
        self,
        config_name: str,
        old_value: Any,
        new_value: Any,
        actor: str = "system",
    ) -> AuditLogEntry:
        return self.log(
            event_type="config_changed",
            actor=actor,
            resource=f"config:{config_name}",
            action="update",
            detail={"config_name": config_name, "old_value": old_value, "new_value": new_value},
        )

    def log_error(
        self,
        error: Exception,
        context: Optional[dict] = None,
        actor: str = "system",
    ) -> AuditLogEntry:
        d = {"error_type": type(error).__name__, "error_message": str(error)}
        if context:
            d["context"] = context
        return self.log(
            event_type="error",
            actor=actor,
            resource="system",
            action="error",
            detail=d,
            status="failure",
        )

    def query(
        self,
        event_type: Optional[str] = None,
        actor: Optional[str] = None,
        resource: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
    ) -> list[AuditLogEntry]:
        results = list(self._entries)
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        if actor:
            results = [e for e in results if e.actor == actor]
        if resource:
            results = [e for e in results if e.resource == resource]
        if start_time:
            results = [e for e in results if e.timestamp >= start_time]
        if end_time:
            results = [e for e in results if e.timestamp <= end_time]
        return results[:limit]

    def export_csv(self, path: str, **query_kwargs: Any) -> None:
        entries = self.query(**query_kwargs) if query_kwargs else list(self._entries)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(_AUDIT_LOG_ENTRY_FIELDS)
            for e in entries:
                row = []
                for field_name in _AUDIT_LOG_ENTRY_FIELDS:
                    val = getattr(e, field_name)
                    if isinstance(val, dict):
                        val = json.dumps(val, ensure_ascii=False)
                    row.append(val)
                writer.writerow(row)

    def get_stats(self) -> dict:
        if not self._entries:
            return {
                "total_entries": 0,
                "by_event_type": {},
                "by_status": {},
                "oldest": None,
                "newest": None,
            }
        by_event_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        timestamps: list[str] = []
        for e in self._entries:
            by_event_type[e.event_type] = by_event_type.get(e.event_type, 0) + 1
            by_status[e.status] = by_status.get(e.status, 0) + 1
            timestamps.append(e.timestamp)
        return {
            "total_entries": len(self._entries),
            "by_event_type": by_event_type,
            "by_status": by_status,
            "oldest": min(timestamps),
            "newest": max(timestamps),
        }

    def _save(self, entry: AuditLogEntry) -> None:
        if self._pg_storage:
            self._save_pg(entry)
        if self._storage_path:
            self._save_file(entry)
        audit_logger = get_audit_logger("audit_trail")
        audit_logger.info(
            "Audit event",
            extra={
                "event_id": entry.event_id,
                "event_type": entry.event_type,
                "actor": entry.actor,
                "resource": entry.resource,
                "action": entry.action,
                "status": entry.status,
            },
        )

    def _save_file(self, entry: AuditLogEntry) -> None:
        try:
            with open(self._storage_path, "a") as f:
                f.write(json.dumps(astuple(entry), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("Failed to write audit log: %s", e)

    def _save_pg(self, entry: AuditLogEntry) -> None:
        try:
            self._pg_storage.execute(
                """
                INSERT INTO audit_log (event_id, timestamp, event_type, actor, resource,
                                       action, detail, ip_address, user_agent, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    entry.event_id,
                    entry.timestamp,
                    entry.event_type,
                    entry.actor,
                    entry.resource,
                    entry.action,
                    json.dumps(entry.detail, ensure_ascii=False),
                    entry.ip_address,
                    entry.user_agent,
                    entry.status,
                ),
            )
        except Exception as e:
            logger.error("Failed to write audit log to PG: %s", e)

    def _rotate_if_needed(self) -> None:
        if not self._storage_path:
            return
        try:
            size = os.path.getsize(self._storage_path)
            if size >= 100 * 1024 * 1024:
                base, ext = os.path.splitext(self._storage_path)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                os.rename(self._storage_path, f"{base}_{ts}{ext}")
        except OSError:
            pass
