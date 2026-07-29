import json
import logging
import sys
from typing import Optional


try:
    from pythonjsonlogger import jsonlogger

    _HAS_JSON_LOGGER = True
except ImportError:
    _HAS_JSON_LOGGER = False


_audit_loggers: dict[str, logging.Logger] = {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "service": getattr(record, "service", ""),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key in ("event_type", "duration_ms", "event_id", "actor", "resource", "action", "status"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        for key in dir(record):
            if key.startswith("_") or key in (
                "args", "msg", "exc_info", "exc_text", "stack_info", "levelno",
                "levelname", "name", "pathname", "filename", "module", "lineno",
                "funcName", "created", "msecs", "relativeCreated", "thread",
                "threadName", "process", "processName", "service", "message",
            ):
                continue
            val = getattr(record, key, None)
            if val is not None and not callable(val):
                log_entry[key] = val
        return json.dumps(log_entry, ensure_ascii=False, default=str)


def setup_structured_logging(
    service_name: str,
    log_level: str = "INFO",
    json_format: bool = True,
) -> logging.Logger:
    logger = logging.getLogger(service_name)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if json_format and _HAS_JSON_LOGGER:
        formatter = jsonlogger.JsonFormatter(
            fmt="%(timestamp)s %(service)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    elif json_format:
        formatter = JsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z")
    else:
        formatter = logging.Formatter(f"[{service_name}] %(levelname)s %(message)s")

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def get_audit_logger(service_name: str) -> logging.Logger:
    if service_name in _audit_loggers:
        return _audit_loggers[service_name]

    logger = logging.getLogger(f"audit.{service_name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)

    if _HAS_JSON_LOGGER:
        formatter = jsonlogger.JsonFormatter(
            fmt="%(timestamp)s %(service)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    else:
        formatter = logging.Formatter(f"[audit.{service_name}] %(levelname)s %(message)s")

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    _audit_loggers[service_name] = logger
    return logger
