#!/usr/bin/env python3
"""creon-bridge v1 - command line entry point (see PHASE2_BRIEF.md).

Runs the headless localhost JSON HTTP bridge on 127.0.0.1 (default port
8100) over the Daishin CybosPlus COM API.  Intended runtime is Windows
32-bit Python 3.11 (``C:\\Users\\jhshi\\Python311-32\\python.exe``).

Usage::

    python run_bridge.py                 # start the HTTP bridge
    python run_bridge.py --port 8200     # custom port
    python run_bridge.py --no-server     # connect/trade-init checks only
    python run_bridge.py --selftest      # stub smoke test, then exit
"""

from __future__ import annotations

import argparse
import sys
import time


def _build_parser() -> argparse.ArgumentParser:
    from bridge.config import DEFAULT_HOST, DEFAULT_PORT

    parser = argparse.ArgumentParser(prog="run_bridge.py", description=__doc__)
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="bind host (default 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="bind port (default 8100)"
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="do not start the HTTP server; run connect checks and exit",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="run a stub smoke test and exit",
    )
    return parser


# --------------------------------------------------------------------- #
# Self test (pure Python fakes, no pywin32, no PLUS connection needed)
# --------------------------------------------------------------------- #
class _FakeCom:
    def __init__(self, progid: str) -> None:
        self.progid = progid
        self.inputs: dict = {}
        self.block_calls = 0
        self.headers: dict = {7: 8812345}
        self.rows: dict = {}
        self._continue = False
        self.block_ret = 0
        self.dib_status = 0
        self.dib_msg = ""
        self.IsConnect = 1
        self.AccountNumber = ("999-99-999999",)
        self.LimitRequestRemainTime = 0

    def GoodsList(self, _account: str, _kind: int) -> tuple:
        return ("01",)

    def TradeInit(self, _n: int) -> int:
        return 0

    def SetInputValue(self, index: int, value) -> None:
        self.inputs[index] = value

    def BlockRequest(self) -> int:
        self.block_calls += 1
        return self.block_ret

    def GetDibStatus(self) -> int:
        return self.dib_status

    def GetDibMsg1(self) -> str:
        return self.dib_msg

    def GetHeaderValue(self, index: int):
        return self.headers.get(index, 0)

    def GetDataValue(self, col: int, row: int):
        values = self.rows.get(col, [])
        return values[row] if row < len(values) else 0

    @property
    def Continue(self) -> bool:
        return self._continue


def _run_selftest() -> int:
    from bridge.orders import cancel_order, place_order
    from bridge.plus import CybosPlus

    made: dict = {}

    def factory(progid: str):
        obj = _FakeCom(progid)
        made[progid] = obj
        return obj

    session = CybosPlus(factory=factory)
    assert session.is_connected() is True, "fake connection should report True"

    result = place_order(session, side="buy", code="005930", qty=10, price=14100)
    order = made["CpTrade.CpTd0311"]
    assert order.inputs[0] == "2", "buy side must map to CpTd0311 input(0)=2"
    assert order.inputs[3] == "A005930", "code must be A-prefixed for trade input"
    assert order.inputs[8] == "01", "limit order must use hoga code 01"
    assert result["price"] == 14100

    result_market = place_order(session, side="sell", code="000660", qty=5, price=0, order_type="market")
    assert made["CpTrade.CpTd0311"].inputs[0] == "1", "sell side must map to input(0)=1"
    assert made["CpTrade.CpTd0311"].inputs[8] == "03", "market order must use hoga code 03"
    assert result_market["price"] == 0

    cancel_order(session, order_no="12345", code="005930", qty=None)
    cancel = made["CpTrade.CpTd0314"]
    assert cancel.inputs[1] == "12345"
    assert cancel.inputs[4] == "A005930"
    assert cancel.inputs[5] == 0, "no qty -> full remaining quantity cancel"

    session.stop()
    print("[selftest] PASS: place buy/sell limit+market, cancel, connect checks")
    return 0


# --------------------------------------------------------------------- #
# Connect checks (no HTTP server)
# --------------------------------------------------------------------- #
def _run_connect_checks() -> int:
    from bridge.orders import default_session
    from bridge.plus import BridgeError

    session = default_session()
    if not session.is_connected():
        print("NOT CONNECTED: CybosPlus link is down (start Creon PLUS).")
        return 1
    try:
        account, goods = session.trade_init()
    except BridgeError as exc:
        print("TRADE INIT FAILED: {0}".format(exc))
        return 1
    print("CONNECTED: account={0} goods={1}".format(account, goods))
    return 0


def _run_server(host: str, port: int) -> int:
    from bridge.config import Config

    config = Config.from_env().with_overrides(host=host, port=port)
    server = _server_with_config(config)
    url = config.endpoint_url()
    print("creon-bridge v1 listening on {0}  (health: {0}/health)".format(url))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
    return 0


def _server_with_config(config):
    from bridge.orders import default_session
    from bridge.realtime import RealtimeHub
    from bridge.server import BridgeApp

    session = default_session()
    hub = RealtimeHub(session)
    return BridgeApp(plus=session, hub=hub, config=config).make_server()


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    # Detached bridge runs redirect stdout into a file, where Python block-buffers
    # and the log stays empty.  Tee into a flushed bridge.log as well.
    try:
        from runner.logutil import install_file_logging

        install_file_logging("bridge.log", note="-- creon-bridge")
    except Exception as exc:  # noqa: BLE001 - logging must never block the bridge
        print("file logging unavailable ({0})".format(exc))
    if args.selftest:
        return _run_selftest()
    if args.no_server:
        return _run_connect_checks()
    return _run_server(args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
