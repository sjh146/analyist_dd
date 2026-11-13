#!/usr/bin/env python3
"""trader-core v1 - command line entry point (see PHASE3_BRIEF.md).

Drives the 64-bit trader-core library on the Windows laptop (or Linux for
dev).  Sub-commands:

    python run_trader_core.py status                     # bridge + feed + account
    python run_trader_core.py scan --dry-run --mock      # gate every feed candidate
    python run_trader_core.py close-all --yes            # liquidate all positions

Common flags: ``--config path.json``, ``--mock`` (in-process MockBridge),
``--no-dry-run``, ``--journal PATH``.  Environment overrides use the
``TRADER_CORE_*`` prefix.  Exit codes: 0 ok, 2 usage, 3 gate-blocked
(scan found nothing actionable), 1 runtime error.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Optional

from trader_core import BridgeClient, BridgeError, FeedError, ScreenerFeedClient
from trader_core.config import Config, now_kst
from trader_core.engine import OrderBlockedError, TraderEngine
from trader_core.rankfeed import make_feed_client
from trader_core.mockbridge import MockBridge

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_GATE_BLOCKED = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_trader_core.py", description=__doc__)
    parser.add_argument(
        "command", choices=["status", "scan", "close-all"],
        help="sub-command to run",
    )
    parser.add_argument(
        "--config", metavar="PATH", default=None,
        help="JSON config file (field names match trader_core.Config)",
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
        "--dry-run", dest="dry_run", action="store_true", default=None,
        help="suppress real orders (default)",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="allow routing orders to the bridge",
    )
    parser.add_argument(
        "--journal", metavar="PATH", default=None,
        help="SQLite journal path (journaling off unless provided)",
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
    # scan options
    parser.add_argument(
        "--screener", metavar="NAME", default=None,
        help="comma-separated screener subset for scan "
             "(close,swing,daytrading)",
    )
    # close-all options
    parser.add_argument(
        "--reason", default="manual close-all",
        help="exit reason recorded for close-all sells",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="close-all: skip the interactive confirmation",
    )
    return parser


# --------------------------------------------------------------------- #
# Config layering (defaults < file < env < CLI)
# --------------------------------------------------------------------- #
def _resolve_config(args: argparse.Namespace) -> Config:
    merged: Dict[str, Any] = {}
    merged.update(Config().as_dict())
    preset_name = getattr(args, "preset", None)
    if preset_name:
        from trader_core.presets import load_preset

        merged.update(load_preset(preset_name))
    if args.config:
        merged.update(Config.from_file(args.config).as_dict())
    merged.update(Config.env_overrides())
    cli: Dict[str, Any] = {}
    if args.dry_run is not None:
        cli["dry_run"] = args.dry_run
    if args.bridge_url:
        cli["bridge_base_url"] = args.bridge_url
    if args.feed_url:
        target = str(args.feed_url).strip()
        if target.lower() == "rank":
            # ``--feed rank`` selects the self-generated ranking source
            # (trader_core/rankfeed.py) instead of an external JSON feed.
            cli["feed_source"] = "rank"
        else:
            cli["feed_url"] = args.feed_url
    if args.screener:
        cli["screener_names"] = [
            name.strip() for name in args.screener.split(",") if name.strip()
        ]
    merged.update(cli)
    cfg = Config._coerce(merged)
    if str(cfg.feed_source).strip().lower() == "rank" and not args.screener:
        # The rank source emits exactly one screener key; trade only that.
        cfg = cfg.with_overrides(screener_names=[cfg.rank_screener])
    return cfg


def _build_bridge(args: argparse.Namespace, cfg: Config):
    if args.mock:
        return MockBridge()
    return BridgeClient(cfg.bridge_base_url, timeout=cfg.request_timeout)


def _build_journal(args: argparse.Namespace, cfg: Config):
    from trader_core.journal import Journal

    path = args.journal or cfg.journal_path
    if not args.journal:
        # Avoid creating the default journal file as a side effect unless the
        # operator opted in with --journal or an explicit env override.
        from trader_core.config import _ENV_PREFIX

        if not any(k.startswith(_ENV_PREFIX + "JOURNAL_PATH") for k in _env_keys()):
            return None
    return Journal(path)


def _env_keys() -> List[str]:
    import os

    return list(os.environ.keys())


def _make_engine(args: argparse.Namespace, cfg: Config) -> TraderEngine:
    bridge = _build_bridge(args, cfg)
    feed_client = make_feed_client(bridge, cfg)
    journal = _build_journal(args, cfg)
    return TraderEngine(bridge, feed_client, cfg, journal)


# --------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------- #
def _run_status(args: argparse.Namespace, cfg: Config) -> int:
    bridge = _build_bridge(args, cfg)
    mode = "mock" if args.mock else "real bridge ({0})".format(cfg.bridge_base_url)
    print("trader-core status   (mode: {0})".format(mode))
    print("config: dry_run={0} feed_max_age={1:.0f}s market_hours_only={2}".format(
        cfg.dry_run, cfg.feed_max_age, cfg.market_hours_only))

    try:
        health = bridge.health()
    except BridgeError as exc:
        print("bridge: UNREACHABLE -> {0}".format(exc))
        return EXIT_ERROR
    print("bridge: connected={0}".format(bool(health.get("connected"))))

    try:
        balance = bridge.balance()
        positions = bridge.positions()
        orders = bridge.open_orders()
    except BridgeError as exc:
        print("bridge account query failed -> {0}".format(exc))
        return EXIT_ERROR
    total_eval = _num(balance.get("total_eval_amount"))
    cash = balance.get("cash")
    if cash is None:
        cash = balance.get("withdrawable", 0)
    equity = balance.get("equity")
    if equity is None or not equity:
        equity = _num(cash) + total_eval
    print("account: equity={0:.0f} cash={1:.0f} positions={2} open_orders={3}".format(
        _num(equity), _num(cash), len(positions), len(orders)))

    try:
        snapshot = ScreenerFeedClient(
            cfg.feed_url, timeout=cfg.request_timeout
        ).fetch()
        fresh = snapshot.is_fresh(now_kst(), cfg.feed_max_age)
        print("feed: generated_at={0} fresh={1} screeners={2}".format(
            snapshot.generated_at.isoformat(),
            fresh,
            ",".join(sorted(snapshot.candidates.keys())) or "-",
        ))
    except FeedError as exc:
        print("feed: UNAVAILABLE -> {0}".format(exc))
    return EXIT_OK


# --------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------- #
def _run_scan(args: argparse.Namespace, cfg: Config) -> int:
    engine = _make_engine(args, cfg)
    try:
        report = engine.scan()
    except BridgeError as exc:
        print("scan aborted: bridge error -> {0}".format(exc))
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - report and continue as blocked
        print("scan aborted: {0}".format(exc))
        return EXIT_ERROR

    if report.feed_error:
        print("scan blocked: feed error -> {0}".format(report.feed_error))
        return EXIT_GATE_BLOCKED
    print("scan: generated_at={0} fresh={1} market_open={2} kill_switch={3}".format(
        report.generated_at.isoformat() if report.generated_at else "-",
        report.feed_fresh,
        report.market_open,
        report.kill_switch,
    ))
    for decision in report.decisions:
        if decision.passed:
            plan = decision.plan
            assert plan is not None
            print("  PASS [{0}] {1} {2} score={3} -> {4} {5} @ {6:.0f} {7}".format(
                decision.screener, decision.code, decision.name or "?",
                decision.score, plan.side, plan.qty, plan.price or 0.0,
                plan.order_type))
        else:
            reason = decision.blocked_reason() or "blocked"
            print("  BLOCKED [{0}] {1} {2} score={3} reason={4}".format(
                decision.screener, decision.code, decision.name or "?",
                decision.score, reason))
    print("scan: {0} decisions, {1} actionable".format(
        len(report.decisions), len(report.actionable)))
    if not report.actionable:
        return EXIT_GATE_BLOCKED
    return EXIT_OK


# --------------------------------------------------------------------- #
# close-all
# --------------------------------------------------------------------- #
def _run_close_all(args: argparse.Namespace, cfg: Config) -> int:
    engine = _make_engine(args, cfg)
    try:
        positions = engine.bridge.positions()
    except BridgeError as exc:
        print("close-all aborted: bridge error -> {0}".format(exc))
        return EXIT_ERROR
    plans = engine.liquidation_plans(positions, reason=args.reason)
    if not plans:
        print("close-all: no open positions to liquidate")
        return EXIT_OK
    print("close-all: {0} position(s) will be sold at market".format(len(plans)))
    for plan in plans:
        print("  SELL {0} {1} x{2} reason={3}".format(
            plan.code, plan.name or "?", plan.qty, plan.entry_reason))
    if not args.yes:
        try:
            answer = input("Proceed? type 'yes' to confirm: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "yes":
            print("close-all: cancelled (no orders placed)")
            return EXIT_OK
    executed = 0
    for plan in plans:
        try:
            result = engine.execute(plan)
        except (OrderBlockedError, BridgeError) as exc:
            print("  SELL {0} FAILED: {1}".format(plan.code, exc))
            continue
        executed += 1
        if result.get("dry_run"):
            print("  SELL {0}: {1}".format(plan.code, result.get("message", "dry-run")))
        else:
            print("  SELL {0}: filled {1} @ {2} ({3})".format(
                plan.code,
                result.get("qty", plan.qty),
                result.get("price", 0),
                result.get("order_no", "?")),
            )
    print("close-all: {0}/{1} sells submitted".format(executed, len(plans)))
    return EXIT_OK


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = _resolve_config(args)
    except (ValueError, OSError) as exc:
        print("config error: {0}".format(exc), file=sys.stderr)
        return EXIT_USAGE
    if args.command == "status":
        return _run_status(args, cfg)
    if args.command == "scan":
        return _run_scan(args, cfg)
    return _run_close_all(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
