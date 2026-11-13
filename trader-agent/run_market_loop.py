#!/usr/bin/env python3
"""Market-hours runner CLI - drive the sim-account trading loop.

Phase 4a command line entry point (see PHASE4_BRIEF.md).  Sub-commands:

    python run_market_loop.py status [--mock] [--config FILE]
    python run_market_loop.py run [--once|--loop] [--dry-run|--live]
                                  [--interval N] [--config FILE] [--mock] [--yes]
    python run_market_loop.py halt [--reason ...] [--config FILE]
    python run_market_loop.py resume [--config FILE]

Safety: ``run`` defaults to ``--once --dry-run``.  Live trading requires an
explicit ``--live`` flag AND either ``--yes``, a ``live_ok`` marker file, or a
typed confirmation.  Config layers: defaults < JSON file < env (``RUNNER_*`` /
``TRADER_CORE_*``) < CLI.  Exit codes: 0 ok, 2 usage, 3 nothing actionable
(e.g. outside market hours with --once), 1 runtime error / halt-by-loss.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

from trader_core import BridgeClient, MockBridge, ScreenerFeedClient
from trader_core.journal import Journal
from trader_core.rankfeed import make_feed_client

from runner.config import RunnerConfig
from runner.logutil import install_file_logging
from runner.state import StateStore

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NO_ACTION = 3

LIVE_OK_MARKER = "live_ok"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_market_loop.py", description=__doc__
    )
    parser.add_argument(
        "command", choices=["status", "run", "halt", "resume"],
        help="sub-command to run",
    )
    parser.add_argument(
        "--config", metavar="PATH", default=None,
        help="JSON config file (runner + trader_core field names)",
    )
    parser.add_argument(
        "--preset", metavar="NAME", default=None,
        help="named preset from presets/ (e.g. paper_safe, rank_only); "
             "layered above defaults and below --config/env/CLI",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="talk to an in-process MockBridge instead of the real bridge",
    )
    parser.add_argument(
        "--bridge", dest="bridge_url", metavar="URL", default=None,
        help="override bridge base URL",
    )
    parser.add_argument(
        "--feed", dest="feed_url", metavar="URL|PATH|rank", default=None,
        help="override screener feed URL (or a local JSON file path); "
             "'rank' selects the self-generated Creon ranking source",
    )
    # run options
    parser.add_argument(
        "--once", dest="mode", action="store_const", const="once",
        default="once", help="run a single cycle then exit (default)",
    )
    parser.add_argument(
        "--loop", dest="mode", action="store_const", const="loop",
        help="keep cycling until market close or a halt",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=None,
        help="suppress real orders (default; engine suppresses bridge calls)",
    )
    parser.add_argument(
        "--live", dest="dry_run", action="store_false",
        help="allow real orders (requires confirmation unless --yes)",
    )
    parser.add_argument(
        "--interval", metavar="N", type=int, default=None,
        help="cycle interval in seconds (overrides config)",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="live mode: skip the interactive confirmation",
    )
    # halt options
    parser.add_argument(
        "--reason", default="manual halt",
        help="reason recorded for halt/resume actions",
    )
    return parser


# ---------------------------------------------------------------------- #
# Config resolution (defaults < file < env < CLI)
# ---------------------------------------------------------------------- #
def _resolve_config(args: argparse.Namespace, live_guard: bool = False) -> RunnerConfig:
    """Layer config sources into a :class:`RunnerConfig`.

    The ``run`` path always resolves to dry-run unless ``--live`` was passed,
    so live trading is impossible by accident even if a file/env set dry_run
    false.
    """
    merged: Dict[str, Any] = {}
    merged.update(RunnerConfig().as_dict())
    preset_name = getattr(args, "preset", None)
    if preset_name:
        from trader_core.presets import load_preset

        merged.update(load_preset(preset_name))
    if args.config:
        merged.update(RunnerConfig.from_file(args.config).as_dict())
    merged.update(RunnerConfig.env_overrides())
    cli: Dict[str, Any] = {}
    if args.dry_run is not None:
        cli["dry_run"] = args.dry_run
    if args.bridge_url:
        cli["bridge_base_url"] = args.bridge_url
    if args.feed_url:
        target = str(args.feed_url).strip()
        if target.lower() == "rank":
            cli["feed_source"] = "rank"
        else:
            cli["feed_url"] = args.feed_url
    if args.interval is not None:
        cli["interval_seconds"] = args.interval
    merged.update(cli)
    cfg = RunnerConfig.from_mapping(merged)
    if live_guard:
        # The run path must never go live implicitly: dry-run unless the
        # operator explicitly passed --live (args.dry_run is False).
        cfg = cfg.with_overrides(dry_run=(args.dry_run is not False))
    if str(cfg.engine.feed_source).strip().lower() == "rank":
        cfg = _apply_rank_mode(cfg)
    return cfg.apply_params()


def _apply_rank_mode(cfg: RunnerConfig) -> RunnerConfig:
    """Point the loop at the self-generated ranking source.

    The rank source emits a single screener key, so the loop trades exactly
    that.  The R5 news gate and the external screener URL belong to the
    BC-250 pipeline and are switched off (the news gate fails *closed* when it
    is enabled but unreachable, which would block every entry).
    """
    from trader_core.rankfeed import DEFAULT_R1_MIN_AVG_SCORE

    screener = cfg.engine.rank_screener
    overrides: Dict[str, Any] = {"screeners": [screener]}
    thresholds = dict(cfg.r1_min_avg_score)
    if screener not in thresholds:
        thresholds[screener] = DEFAULT_R1_MIN_AVG_SCORE
    overrides["r1_min_avg_score"] = thresholds
    if cfg.news_gate_url == RunnerConfig().news_gate_url:
        overrides["news_gate_url"] = ""  # no BC-250 news gate without BC-250
    return cfg.with_overrides(**overrides)


def _confirm_live(args: argparse.Namespace) -> bool:
    """Return True when live trading is explicitly authorised."""
    if args.dry_run is not False:
        return True  # not live
    if args.yes:
        return True
    if os.path.exists(LIVE_OK_MARKER):
        return True
    try:
        answer = input("LIVE MODE: type 'yes' to allow real orders: ")
    except EOFError:
        answer = ""
    return answer.strip().lower() == "yes"


def _state_store(cfg: RunnerConfig) -> StateStore:
    return StateStore(cfg.state_path)


def _journal(cfg: RunnerConfig) -> Optional[Journal]:
    """Open the journal only when the file already exists (status query)."""
    if os.path.exists(cfg.journal_path):
        return Journal(cfg.journal_path)
    return None


def _bridge(args: argparse.Namespace, cfg: RunnerConfig):
    engine_cfg = cfg.engine_config()
    if args.mock:
        return MockBridge()
    return BridgeClient(engine_cfg.bridge_base_url, timeout=engine_cfg.request_timeout)


# ---------------------------------------------------------------------- #
# status
# ---------------------------------------------------------------------- #
def _run_status(args: argparse.Namespace, cfg: RunnerConfig) -> int:
    state_store = _state_store(cfg)
    state = state_store.read()
    has_state = state_store.exists()

    print("runner status   (mode: {0})".format("mock" if args.mock else "live bridge"))
    print("config: dry_run={0} screeners={1} interval={2}s".format(
        cfg.dry_run, ",".join(cfg.screeners), cfg.interval_seconds))

    if not has_state:
        print("loop_state: no state yet ({0})".format(cfg.state_path))
    else:
        daily = state.get("daily", {})
        print("loop_state: halt={0} reason={1!r} date={2}".format(
            state["loop"]["halt"], state["loop"]["halt_reason"], daily.get("date")))
        print("  daily: start_equity={0} entries={1} closed={2} pnl={3}".format(
            daily.get("start_equity"), daily.get("entries"),
            daily.get("closed"), daily.get("closed_pnl")))
        if state.get("last_summary"):
            print("  last_summary: {0}".format(state["last_summary"]))

    journal = _journal(cfg)
    if journal is None:
        print("journal: none yet ({0})".format(cfg.journal_path))
    else:
        try:
            summary = journal.summary()
            trades = journal.trade_count_today()
            print("journal: closed_trades={0} wins={1} losses={2} total_pnl={3:.0f} "
                  "today_rows={4}".format(
                      summary["trades"], summary["wins"], summary["losses"],
                      summary["total_pnl"], trades))
        finally:
            journal.close()

    try:
        bridge = _bridge(args, cfg)
        health = bridge.health()
        balance = bridge.balance()
        positions = bridge.positions()
    except Exception as exc:  # noqa: BLE001 - status must degrade gracefully
        print("bridge: UNREACHABLE -> {0}".format(exc))
        return EXIT_ERROR
    equity = float(balance.get("equity") or 0)
    if not equity:
        equity = float(balance.get("cash") or 0) + float(
            balance.get("total_eval_amount") or 0)
    print("bridge: connected={0} equity={1:.0f} positions={2}".format(
        bool(health.get("connected")), equity, len(positions)))
    return EXIT_OK


# ---------------------------------------------------------------------- #
# run
# ---------------------------------------------------------------------- #
def _run_run(args: argparse.Namespace, cfg: RunnerConfig) -> int:
    from runner.loop import MarketLoop

    if not _confirm_live(args):
        print("run aborted: live trading requires --yes (or a '{0}' marker "
              "file or typing confirmation)".format(LIVE_OK_MARKER),
              file=sys.stderr)
        return EXIT_USAGE

    engine_cfg = cfg.engine_config()
    # Detached runs redirect stdout to a file, where Python block-buffers and the
    # log stays empty for hours.  Tee everything into a flushed file instead.
    log_path = getattr(cfg, "log_path", "") or "runner.log"
    install_file_logging(log_path, note="-- {0}".format(" ".join(sys.argv[1:])))
    print("logging to {0}".format(os.path.abspath(log_path)))
    bridge = _bridge(args, cfg)
    feed_client = make_feed_client(bridge, engine_cfg)
    loop = MarketLoop(bridge, feed_client, cfg)
    try:
        report = loop.run_once() if args.mode == "once" else loop.run_forever()
    except KeyboardInterrupt:
        print("run interrupted by user")
        return EXIT_OK
    finally:
        loop.journal.close()

    _print_report(report)
    if report.halted:
        loss = report.halt_reason and "loss" in report.halt_reason.lower()
        if loss:
            return EXIT_ERROR
        return EXIT_OK
    if report.closed:
        return EXIT_OK
    if report.phase == "idle":
        return EXIT_NO_ACTION
    return EXIT_OK


def _print_report(report: Any) -> None:
    print("cycle: ts={0} phase={1} market_open={2} checked={3} fresh={4}".format(
        report.ts.isoformat(), report.phase, report.market_open,
        report.checked, report.fresh))
    print("cycle: decisions={0} r1_blocked={1} plans={2} acted={3} exits={4}".format(
        report.decisions, report.blocked_screeners, report.plans,
        report.acted, report.exits))
    if report.halted:
        print("cycle: HALTED reason={0}".format(report.halt_reason))
    for reason in report.reasons:
        print("  - {0}".format(reason))


# ---------------------------------------------------------------------- #
# halt / resume
# ---------------------------------------------------------------------- #
def _run_halt(args: argparse.Namespace, cfg: RunnerConfig) -> int:
    state_store = _state_store(cfg)
    state = state_store.read()
    state["loop"]["halt"] = True
    state["loop"]["halt_reason"] = args.reason
    state_store.write(state)
    print("halt: soft-stop flag set (reason={0!r})".format(args.reason))
    return EXIT_OK


def _run_resume(args: argparse.Namespace, cfg: RunnerConfig) -> int:
    state_store = _state_store(cfg)
    state = state_store.read()
    state["loop"]["halt"] = False
    state["loop"]["halt_reason"] = ""
    state_store.write(state)
    print("resume: soft-stop flag cleared (kill-switch file untouched)")
    return EXIT_OK


# ---------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = _resolve_config(args, live_guard=(args.command == "run"))
    except (ValueError, OSError) as exc:
        print("config error: {0}".format(exc), file=sys.stderr)
        return EXIT_USAGE
    if args.command == "status":
        return _run_status(args, cfg)
    if args.command == "run":
        return _run_run(args, cfg)
    if args.command == "halt":
        return _run_halt(args, cfg)
    return _run_resume(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
