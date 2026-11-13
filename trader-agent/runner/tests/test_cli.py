"""run_market_loop.py CLI parser + safety-guard tests."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

import run_market_loop as cli


class CliParserTest(unittest.TestCase):
    def _parse(self, *argv: str):
        parser = cli._build_parser()
        return parser.parse_args(list(argv))

    def test_run_defaults_dry_run_true_once(self) -> None:
        args = self._parse("run")
        self.assertEqual(args.mode, "once")
        self.assertIsNone(args.dry_run)  # resolved to True below
        cfg = cli._resolve_config(args, live_guard=True)
        self.assertTrue(cfg.dry_run)

    def test_live_flag_turns_off_dry_run(self) -> None:
        args = self._parse("run", "--live")
        cfg = cli._resolve_config(args, live_guard=True)
        self.assertFalse(cfg.dry_run)

    def test_file_env_live_cannot_imply_live_without_flag(self) -> None:
        tmp = tempfile.mkdtemp(prefix="runner-cli-")
        try:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"dry_run": false}')
            args = self._parse("run", "--config", path)
            cfg = cli._resolve_config(args, live_guard=True)
            self.assertTrue(cfg.dry_run, "run path must never go live implicitly")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_config_file_and_interval_flags(self) -> None:
        tmp = tempfile.mkdtemp(prefix="runner-cli-")
        try:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"interval_seconds": 45, "screeners": ["swing"]}')
            args = self._parse("run", "--config", path, "--interval", "120")
            cfg = cli._resolve_config(args, live_guard=True)
            self.assertEqual(cfg.interval_seconds, 120)  # CLI beats file
            self.assertEqual(cfg.screeners, ["swing"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_resolves_without_live_guard(self) -> None:
        args = self._parse("status")
        cfg = cli._resolve_config(args, live_guard=False)
        self.assertTrue(cfg.dry_run)


class LiveGuardTest(unittest.TestCase):
    def test_dry_run_never_asks(self) -> None:
        args = cli._build_parser().parse_args(["run"])
        self.assertTrue(cli._confirm_live(args))

    def test_live_without_yes_requires_confirmation(self) -> None:
        args = cli._build_parser().parse_args(["run", "--live"])
        with mock.patch("builtins.input", return_value="nope"):
            self.assertFalse(cli._confirm_live(args))
        with mock.patch("builtins.input", return_value="yes"):
            self.assertTrue(cli._confirm_live(args))

    def test_live_yes_bypasses_confirmation(self) -> None:
        args = cli._build_parser().parse_args(["run", "--live", "--yes"])
        self.assertTrue(cli._confirm_live(args))

    def test_live_marker_file_bypasses_confirmation(self) -> None:
        args = cli._build_parser().parse_args(["run", "--live"])
        with mock.patch.object(cli.os.path, "exists", return_value=True):
            self.assertTrue(cli._confirm_live(args))
        with mock.patch.object(
            cli.os.path, "exists",
            side_effect=lambda p: p == cli.LIVE_OK_MARKER,
        ):
            self.assertTrue(cli._confirm_live(args))

    def test_halt_default_reason(self) -> None:
        args = cli._build_parser().parse_args(["halt"])
        self.assertEqual(args.reason, "manual halt")
        args2 = cli._build_parser().parse_args(["halt", "--reason", "daily_loss_limit"])
        self.assertEqual(args2.reason, "daily_loss_limit")


if __name__ == "__main__":
    unittest.main()
