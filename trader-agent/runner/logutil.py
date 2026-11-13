"""Flushing file logging for the detached processes.

Why this exists: the loop and bridge are started detached with
``start ... >> loop.out.log``.  When Python's stdout is a file (not a TTY) it
block-buffers, so a healthy process can run for hours with an empty log file --
exactly what happened in the 2026-09-10 session.  Redirected logs are therefore
useless for live debugging.

``install_file_logging`` tees ``sys.stdout``/``sys.stderr`` into a file that is
flushed after every write, so the log is readable while the process runs.  It
never raises: losing log output must never take down trading.
"""

from __future__ import annotations

import io
import os
import sys
import time
from datetime import datetime
from typing import Any, List, Optional

DEFAULT_MAX_BYTES = 5_000_000  # rotate at ~5 MB, one generation kept


class _Tee:
    """Write-through stream that fans out to several streams."""

    def __init__(self, streams: List[Any]) -> None:
        self._streams = [stream for stream in streams if stream is not None]

    def write(self, text: str) -> int:
        written = 0
        for stream in self._streams:
            try:
                result = stream.write(text)
                stream.flush()
                if isinstance(result, int):
                    written = max(written, result)
            except Exception:  # noqa: BLE001 - logging must never break the app
                continue
        return written or len(text)

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                continue

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        for stream in self._streams:
            try:
                return stream.fileno()
            except Exception:  # noqa: BLE001
                continue
        raise io.UnsupportedOperation("no underlying file descriptor")


class FileLogger:
    """Append-only, line-flushed log file with one-generation rotation."""

    def __init__(self, path: str, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.path = path
        self.max_bytes = int(max_bytes)
        self._handle: Optional[Any] = None
        self._open()

    # -- internals ----------------------------------------------------- #
    def _open(self) -> None:
        self._rotate_if_needed()
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            self._handle = open(self.path, "a", encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - no disk/no permission: log to console only
            self._handle = None

    def _rotate_if_needed(self) -> None:
        try:
            if self.max_bytes <= 0 or not os.path.exists(self.path):
                return
            if os.path.getsize(self.path) < self.max_bytes:
                return
            backup = "{0}.1".format(self.path)
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(self.path, backup)
        except Exception:  # noqa: BLE001
            pass

    def _stamp(self) -> str:
        now = datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S") + ".{0:03d}".format(
            now.microsecond // 1000
        )

    # -- public API ---------------------------------------------------- #
    @property
    def available(self) -> bool:
        return self._handle is not None

    def log(self, line: str) -> None:
        """Write one timestamped, flushed line."""
        text = str(line).rstrip("\n")
        stamped = "[{0}] {1}".format(self._stamp(), text) if text else ""
        if self._handle is None:
            return
        try:
            self._handle.write(stamped + "\n")
            self._handle.flush()
        except Exception:  # noqa: BLE001
            pass

    __call__ = log

    def header(self, note: str = "") -> None:
        self.log(
            "=== log opened pid={0} cwd={1} {2}".format(
                os.getpid(), os.getcwd(), note
            ).strip()
        )

    def close(self) -> None:
        try:
            if self._handle is not None:
                self._handle.flush()
                self._handle.close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._handle = None


def install_file_logging(
    path: str, max_bytes: int = DEFAULT_MAX_BYTES, note: str = ""
) -> FileLogger:
    """Tee stdout/stderr into *path*; return the logger.

    The returned object (and the global reference kept here) must stay alive
    for the tee to work.
    """
    logger = FileLogger(path, max_bytes=max_bytes)
    if logger.available:
        logger.header(note)
        # Tee the *current* streams so installs compose (and so a caller that
        # already redirected stdout keeps receiving output).
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout = _Tee([stdout, logger._handle])
        sys.stderr = _Tee([stderr, logger._handle])
    _INSTALLED.append(logger)
    return logger


_INSTALLED: List[FileLogger] = []


def uninstall_file_logging() -> None:
    """Restore the original streams (used by tests)."""
    global _INSTALLED
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    for logger in _INSTALLED:
        logger.close()
    _INSTALLED = []


def sleep_flush(seconds: float) -> None:
    """Sleep in small steps so log lines appear promptly on some filesystems."""
    deadline = time.time() + max(0.0, seconds)
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


def log_path_for(base: str) -> str:
    """Return the default log path for *base* (e.g. ``runner``/``bridge``)."""
    return os.path.join(os.getcwd(), "{0}.log".format(base))
