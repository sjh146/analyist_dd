"""Realtime quote hub for CybosPlus (DsCbo1.StockCur / CpConclusion).

Design (see PHASE2_BRIEF.md and api_notes.md):

* Realtime events are COM events and must be serviced on an STA thread that
  runs a message pump.  :class:`RealtimeHub` owns such a dedicated pump
  thread.
* All ``DsCbo1.StockCur`` objects are created and subscribed from inside the
  pump thread; other threads only enqueue (action, codes) commands, so COM
  objects keep apartment affinity.
* The pump loop drains the command queue and repeatedly calls
  ``pythoncom.PumpWaitingMessages()`` between short sleeps.  Daishin samples
  relied on Qt's event loop or ``win32event.MsgWaitForMultipleObjects``; we
  use the headless ``PumpWaitingMessages`` equivalent so nothing needs a GUI.
* :meth:`latest_quotes` returns a thread-safe snapshot dict keyed by the
  *plain* 6 digit code.

The optional *order execution callback* is wired to ``DsCbo1.CpConclusion``
(realtime order/fill events) as shown by the samples
``creon_algorism3__01__stock_IOC_FOK...`` and ``creon_algorism3__08__...``.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Dict, List, Optional

from .codes import to_plain_code, to_trade_code
from .orders import default_session
from .plus import CybosPlus, _num

_POLL_SECONDS = 0.02


class _StockCurEvents:
    """Event handler bound to a single DsCbo1.StockCur subscription.

    Instances are created by ``win32com.client.WithEvents`` (no constructor
    arguments, mirroring the Daishin samples); the hub then attaches
    ``hub`` / ``plain`` / ``client`` attributes.
    """

    hub: "RealtimeHub" = None  # type: ignore[assignment]
    plain: str = ""
    client = None

    def OnReceived(self) -> None:
        c = self.client
        if c is None:
            return
        try:
            quote = {
                "code": self.plain,
                "name": c.GetHeaderValue(1),
                "diff": c.GetHeaderValue(2),
                "current": c.GetHeaderValue(13),
                "volume": c.GetHeaderValue(9),
                "cont_volume": c.GetHeaderValue(17),
                "time": c.GetHeaderValue(18),
                "ex_flag": _num(c.GetHeaderValue(19)),
                "ts": time.time(),
            }
            self.hub._store_quote(self.plain, quote)
        except Exception:  # noqa: BLE001 - never let a COM tick kill the pump
            return


class _ConclusionEvents:
    """Event handler for DsCbo1.CpConclusion realtime order/execution data.

    Header indexes follow ``creon_algorism3__01__stock_IOC_FOK_order...`` and
    ``creon_algorism3__08__...``.
    """

    hub: "RealtimeHub" = None  # type: ignore[assignment]
    client = None

    def OnReceived(self) -> None:
        c = self.client
        if c is None:
            return
        try:
            event: Dict[str, object] = {
                "flag": str(c.GetHeaderValue(14)),   # 1 fill 2 confirm 3 rejected 4 received
                "qty": c.GetHeaderValue(3),
                "price": c.GetHeaderValue(4),
                "order_no": c.GetHeaderValue(5),
                "original_order_no": c.GetHeaderValue(6),
                "code": _plain(c.GetHeaderValue(9)),
                "bs_code": str(c.GetHeaderValue(12)),  # 1 sell 2 buy
                "amend_flag": str(c.GetHeaderValue(16)),  # 1 normal 2 amend 3 cancel
                "hoga_code": str(c.GetHeaderValue(18)),
                "condition_code": str(c.GetHeaderValue(19)),
                "book_price": c.GetHeaderValue(21),
                "sellable_qty": c.GetHeaderValue(22),
                "balance_qty": c.GetHeaderValue(23),
                "ts": time.time(),
            }
            self.hub._store_execution(event)
        except Exception:  # noqa: BLE001 - never let a COM tick kill the pump
            return


def _plain(value) -> str:
    try:
        return to_plain_code(str(value).strip())
    except ValueError:
        return str(value)


class RealtimeHub:
    """Owns realtime subscriptions and the background STA message pump."""

    def __init__(
        self,
        plus: Optional[CybosPlus] = None,
        on_execution: Optional[Callable[[Dict[str, object]], None]] = None,
        track_executions: bool = False,
    ) -> None:
        self._plus = plus if plus is not None else default_session()
        self._on_execution = on_execution
        self._track_executions = track_executions
        self._lock = threading.RLock()
        self._subscribed: set = set()            # plain codes we want
        self._latest: Dict[str, Dict[str, object]] = {}
        self._executions: List[Dict[str, object]] = []
        self._active: Dict[str, tuple] = {}      # plain -> (stockcur obj, handler)
        self._conclusion: Optional[tuple] = None
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._com_ok = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def subscribe(self, codes: List[str]) -> None:
        """Subscribe to realtime current price for *codes* (plain 6 digits)."""
        plain = {to_plain_code(c) for c in codes}
        with self._lock:
            self._subscribed.update(plain)
        self._start()
        self._queue.put(("subscribe", sorted(plain)))

    def unsubscribe(self, codes: List[str]) -> None:
        """Unsubscribe *codes*; their cached latest quote stays available."""
        plain = set()
        with self._lock:
            for c in codes:
                pc = to_plain_code(c)
                self._subscribed.discard(pc)
                plain.add(pc)
        self._queue.put(("unsubscribe", sorted(plain)))

    def latest_quotes(self) -> Dict[str, Dict[str, object]]:
        """Return a thread-safe snapshot of the latest quotes (plain codes)."""
        with self._lock:
            return {k: dict(v) for k, v in self._latest.items()}

    def executions(self) -> List[Dict[str, object]]:
        """Return recorded execution events (only when track_executions)."""
        with self._lock:
            return [dict(e) for e in self._executions]

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._start()

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._pump_main, name="cybos-realtime-pump", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the pump thread and unsubscribe all active realtime objects."""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    # ------------------------------------------------------------------ #
    # Pump thread body
    # ------------------------------------------------------------------ #
    def _pump_main(self) -> None:
        self._init_com()
        try:
            if self._on_execution is not None or self._track_executions:
                self._subscribe_conclusion()
            while not self._stop.is_set():
                self._drain_commands()
                self._pump_once()
                time.sleep(_POLL_SECONDS)
        finally:
            self._teardown_com()

    def _init_com(self) -> None:
        try:
            import pythoncom  # noqa: PLC0415
        except ImportError:
            self._com_ok = False
            return
        try:
            pythoncom.CoInitialize()
            self._com_ok = True
        except Exception:  # noqa: BLE001
            self._com_ok = False

    def _pump_once(self) -> None:
        if not self._com_ok:
            return
        try:
            import pythoncom  # noqa: PLC0415
        except ImportError:
            return
        try:
            pythoncom.PumpWaitingMessages()
        except Exception:  # noqa: BLE001
            pass

    def _teardown_com(self) -> None:
        self._unsubscribe_all_objects()
        if not self._com_ok:
            return
        try:
            import pythoncom  # noqa: PLC0415
        except ImportError:
            return
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    # Command handling (runs on pump thread)
    # ------------------------------------------------------------------ #
    def _drain_commands(self) -> None:
        try:
            while True:
                command = self._queue.get_nowait()
                self._handle_command(command)
        except queue.Empty:
            return

    def _handle_command(self, command: tuple) -> None:
        kind, codes = command
        if kind == "subscribe":
            for code in codes:
                self._subscribe_one(code)
        elif kind == "unsubscribe":
            for code in codes:
                self._unsubscribe_one(code)

    def _subscribe_one(self, plain: str) -> None:
        if not self._com_ok:
            return
        try:
            obj = self._plus.create("stock_cur")
            handler = self._bind_events(obj, _StockCurEvents)
            handler.hub = self
            handler.plain = plain
            handler.client = obj
            obj.SetInputValue(0, to_trade_code(plain))
            obj.Subscribe()
            with self._lock:
                self._active[plain] = (obj, handler)
        except Exception:  # noqa: BLE001 - subscribe failure must not kill pump
            return

    def _subscribe_conclusion(self) -> None:
        if not self._com_ok or self._conclusion is not None:
            return
        try:
            obj = self._plus.create("conclusion")
            handler = self._bind_events(obj, _ConclusionEvents)
            handler.hub = self
            handler.client = obj
            obj.Subscribe()
            self._conclusion = (obj, handler)
        except Exception:  # noqa: BLE001
            return

    def _unsubscribe_one(self, plain: str) -> None:
        with self._lock:
            entry = self._active.pop(plain, None)
        if entry is None:
            return
        obj, _handler = entry
        try:
            obj.Unsubscribe()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _bind_events(obj, event_class):
        """Bind a WithEvents sink, mirroring the Daishin sample pattern."""
        import win32com.client  # noqa: PLC0415 - lazy import

        return win32com.client.WithEvents(obj, event_class)

    def _unsubscribe_all_objects(self) -> None:
        with self._lock:
            active = self._active
            self._active = {}
            conclusion = self._conclusion
            self._conclusion = None
        for entry in active.values():
            try:
                entry[0].Unsubscribe()
            except Exception:  # noqa: BLE001
                pass
        if conclusion is not None:
            try:
                conclusion[0].Unsubscribe()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    # Thread-safe storage
    # ------------------------------------------------------------------ #
    def _store_quote(self, plain: str, quote: Dict[str, object]) -> None:
        with self._lock:
            if plain not in self._subscribed:
                return
            self._latest[plain] = quote

    def _store_execution(self, event: Dict[str, object]) -> None:
        with self._lock:
            if self._track_executions:
                self._executions.append(event)
        cb = self._on_execution
        if cb is not None:
            try:
                cb(event)
            except Exception:  # noqa: BLE001 - user callbacks must not kill pump
                return
