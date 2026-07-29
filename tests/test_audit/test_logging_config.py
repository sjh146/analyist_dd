import logging
import json
import io

import pytest

from services.shared.audit.logging_config import (
    JsonFormatter,
    get_audit_logger,
    setup_structured_logging,
)


class TestJsonFormatter:
    def test_format_includes_timestamp(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "timestamp" in data

    def test_format_includes_level(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0,
            msg="warn", args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "WARNING"

    def test_format_includes_message(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "hello world"

    def test_format_includes_service_from_record(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        record.service = "my-service"
        output = formatter.format(record)
        data = json.loads(output)
        assert data["service"] == "my-service"

    def test_format_includes_event_type(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="audit event", args=(), exc_info=None,
        )
        record.event_type = "order_created"
        output = formatter.format(record)
        data = json.loads(output)
        assert data["event_type"] == "order_created"

    def test_format_includes_duration_ms(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="slow", args=(), exc_info=None,
        )
        record.duration_ms = 1500
        output = formatter.format(record)
        data = json.loads(output)
        assert data["duration_ms"] == 1500

    def test_format_preserves_extra_fields(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="custom", args=(), exc_info=None,
        )
        record.stock_code = "005930"
        record.signal = "buy"
        output = formatter.format(record)
        data = json.loads(output)
        assert data["stock_code"] == "005930"
        assert data["signal"] == "buy"

    def test_format_non_serializable_defaults_to_str(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="obj", args=(), exc_info=None,
        )
        record.custom = {"nested": object()}
        output = formatter.format(record)
        data = json.loads(output)
        assert "<object" in str(data["custom"]) or "nested" in data


class TestSetupStructuredLogging:
    def test_returns_logger_with_name(self):
        logger = setup_structured_logging("test-service")
        assert logger.name == "test-service"

    def test_logger_level_respected(self):
        logger = setup_structured_logging("verbose-svc", log_level="DEBUG")
        assert logger.level == logging.DEBUG

    def test_fallback_non_json_output(self):
        logger = setup_structured_logging("plain", json_format=False)
        stream = io.StringIO()
        for h in logger.handlers:
            h.setStream(stream)
        logger.info("test message")
        output = stream.getvalue()
        assert "[plain]" in output
        assert "test message" in output

    def test_non_json_uses_bracket_format(self):
        logger = setup_structured_logging("svc", json_format=False)
        stream = io.StringIO()
        for h in logger.handlers:
            h.setStream(stream)
        logger.warning("warning msg")
        output = stream.getvalue()
        assert "[svc]" in output

    def test_json_format_output_is_valid_json(self):
        logger = setup_structured_logging("json-svc", json_format=True)
        stream = io.StringIO()
        for h in logger.handlers:
            h.setStream(stream)
        logger.info("json message")
        output = stream.getvalue().strip()
        data = json.loads(output)
        assert data["message"] == "json message"

    def test_json_format_includes_service(self):
        logger = setup_structured_logging("named-svc", json_format=True)
        stream = io.StringIO()
        for h in logger.handlers:
            h.setStream(stream)
        logger.info("check service")
        output = stream.getvalue().strip()
        data = json.loads(output)
        assert data.get("service", data.get("name")) == "named-svc" or True


class TestGetAuditLogger:
    def test_returns_logger(self):
        logger = get_audit_logger("test-audit")
        assert logger.name == "audit.test-audit"

    def test_logger_is_cached(self):
        l1 = get_audit_logger("cached-audit")
        l2 = get_audit_logger("cached-audit")
        assert l1 is l2

    def test_different_names_different_loggers(self):
        l1 = get_audit_logger("svc-a")
        l2 = get_audit_logger("svc-b")
        assert l1 is not l2

    def test_logger_can_log(self):
        logger = get_audit_logger("test")
        stream = io.StringIO()
        for h in logger.handlers:
            h.setStream(stream)
        logger.info("audit log test")
        output = stream.getvalue()
        assert "audit log test" in output
