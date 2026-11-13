"""Tests for the flushed file logger (detached-run visibility).

The bug: with ``start ... >> loop.out.log`` Python block-buffers stdout, so a
healthy detached loop left an empty log file.  These tests pin the fix: a line
written through the tee is on disk *immediately*, without closing anything.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest

from runner.logutil import FileLogger, install_file_logging, uninstall_file_logging


class FileLoggerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "x.log")

    def tearDown(self) -> None:
        uninstall_file_logging()
        self._tmp.cleanup()

    def _read(self, path: str = "") -> str:
        with open(path or self.path, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_line_is_visible_before_close(self) -> None:
        logger = FileLogger(self.path)
        logger.log("hello")
        self.assertIn("hello", self._read())  # no close() yet
        logger.close()

    def test_lines_are_timestamped(self) -> None:
        logger = FileLogger(self.path)
        logger.log("cycle done")
        logger.close()
        line = self._read().strip()
        self.assertTrue(line.startswith("["), line)
        self.assertIn("cycle done", line)

    def test_callable_interface(self) -> None:
        logger = FileLogger(self.path)
        logger("as a callback")
        logger.close()
        self.assertIn("as a callback", self._read())

    def test_appends_across_instances(self) -> None:
        FileLogger(self.path).log("first")
        FileLogger(self.path).log("second")
        text = self._read()
        self.assertLess(text.index("first"), text.index("second"))

    def test_rotation_keeps_one_generation(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("x" * 500)
        logger = FileLogger(self.path, max_bytes=100)
        logger.log("new line")
        logger.close()
        self.assertTrue(os.path.exists(self.path + ".1"))
        self.assertIn("new line", self._read())
        self.assertNotIn("xxxx", self._read())

    def test_unwritable_path_does_not_raise(self) -> None:
        logger = FileLogger(os.path.join(self._tmp.name, "nope", "\0bad", "x.log"))
        self.assertFalse(logger.available)
        logger.log("dropped")  # must not raise
        logger.close()

    def test_header_records_pid_and_cwd(self) -> None:
        logger = FileLogger(self.path)
        logger.header("-- self test")
        logger.close()
        text = self._read()
        self.assertIn("pid={0}".format(os.getpid()), text)
        self.assertIn("-- self test", text)


class InstallFileLoggingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "runner.log")
        self._stdout = sys.stdout
        self._stderr = sys.stderr

    def tearDown(self) -> None:
        uninstall_file_logging()
        self._tmp.cleanup()

    def test_print_lands_in_the_file_and_on_the_console(self) -> None:
        buffer = io.StringIO()
        sys.stdout = buffer
        install_file_logging(self.path)
        print("cycle done (live)")
        text = open(self.path, encoding="utf-8").read()
        console = buffer.getvalue()
        uninstall_file_logging()
        self.assertIn("cycle done (live)", text)
        self.assertIn("cycle done (live)", console)

    def test_stderr_is_captured_too(self) -> None:
        sys.stderr = io.StringIO()
        install_file_logging(self.path)
        print("traceback-ish message", file=sys.stderr)
        text = open(self.path, encoding="utf-8").read()
        uninstall_file_logging()
        self.assertIn("traceback-ish message", text)

    def test_uninstall_restores_streams(self) -> None:
        install_file_logging(self.path)
        self.assertNotIsInstance(sys.stdout, io.StringIO)
        uninstall_file_logging()
        self.assertIs(sys.stdout, sys.__stdout__)
        self.assertIs(sys.stderr, sys.__stderr__)

    def test_tee_reports_not_a_tty_and_delegates_fileno(self) -> None:
        logger = FileLogger(self.path)
        from runner.logutil import _Tee

        tee = _Tee([sys.__stdout__, logger._handle])
        self.assertFalse(tee.isatty())
        self.assertIsInstance(tee.fileno(), int)  # libraries may call fileno()
        logger.close()


if __name__ == "__main__":
    unittest.main()
