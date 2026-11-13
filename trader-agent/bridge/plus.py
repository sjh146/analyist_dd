"""CybosPlus COM session wrapper and central dispatch-name registry.

Design goals from PHASE2_BRIEF.md:

* All COM object creation goes through the :class:`CybosPlus` factory so
  tests can inject fakes (a plain ``factory(progid) -> obj`` callable).
* ``win32com`` / ``pythoncom`` are imported lazily, inside functions, never
  at module top level, so this package imports and unit-tests on Linux/CI.
* CybosPlus COM is apartment threaded.  Blocking ``BlockRequest`` calls are
  serialised on a single dedicated STA worker thread (own ``CoInitialize``)
  so they never block the realtime message pump (see :mod:`bridge.realtime`).
* Dispatch (progid) names are centralised here in :data:`DISPATCH_NAMES`.

Error model
-----------
Any operational failure raises :class:`BridgeError` carrying a stable
``code``, a human ``message`` and an optional ``action`` hint.  The HTTP
layer converts these into the ``{ok: false, error: {...}}`` envelope.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Dict, Optional, Tuple

# Central registry of symbolic COM object names -> CybosPlus progids.
# Every COM class used by the bridge appears here and nowhere else.
DISPATCH_NAMES: Dict[str, str] = {
    # Connection / utility objects
    "cybos": "CpUtil.CpCybos",
    "code_mgr": "CpUtil.CpCodeMgr",
    "td_util": "CpTrade.CpTdUtil",
    # Order / cancel
    "order": "CpTrade.CpTd0311",     # new buy/sell order
    "modify": "CpTrade.CpTd0313",    # amend order (kept for reference)
    "cancel": "CpTrade.CpTd0314",    # cancel order
    # Trade queries
    "open_orders": "CpTrade.CpTd5339",  # outstanding (unfilled) orders
    "positions": "CpTrade.CpTd6033",    # stock balance per position
    "buyable": "CpTrade.CpTdNew5331A",  # per-account buyable cash/qty (account order query)
    "daily_pnl": "CpTrade.CpTd6032",    # executed-basis daily P&L
    # Quotes
    "stock_mst": "DsCbo1.StockMst",   # single stock snapshot (BlockRequest)
    "stock_cur": "DsCbo1.StockCur",   # realtime per-stock current price
    "conclusion": "DsCbo1.CpConclusion",  # realtime order/execution events
    # Signal sources (candidate generation without an external feed)
    "rank": "CpSysDib.CpSvrNew7043",  # 당일 상승률/하락률 상위 랭킹 (see bridge/signals.py)
}

# CybosPlus BlockRequest return codes (see api_notes.md "Communication errors")
_RET_OK = 0
_RET_LIMIT = 4  # continuous comms limit count exceeded (re-request after LimitRequestRemainTime)

# Idle poll of the blocking worker's job queue: the worker must also pump COM
# messages while it waits (see :meth:`CybosPlus.pump_waiting_messages`).
_IDLE_POLL_SECONDS = 0.02
_MAX_IDLE_PUMP = 50  # bound the idle pump so queued jobs are never starved

_ACTION_RETRY_LATER = "wait_for_request_slot_and_retry"
_ACTION_CHECK_REMOTE = "check_order/account state at the broker"
_ACTION_INSTALL_PYWIN32 = "run on Windows 32-bit Python with pywin32 installed"

# How many times a COM-level "request never went out" error is retried before
# surfacing (see ``_is_transient_com_error``).
_TRANSIENT_RETRIES = 3

# Markers of CybosPlus's transient DibRq failure ("다시 요청하십시오" = request
# again).  Matched as substrings so the message survives cp949/utf-8 mangling;
# the HRESULT is checked too because the text can be empty in some builds.
_TRANSIENT_MARKERS = (
    "DibRq",
    "다시 요청",
    "통신요청 실패",
    "RQID",
)
_TRANSIENT_HRESULTS = (-2147352567,)


def _is_transient_com_error(exc: BaseException) -> bool:
    """Return True for CybosPlus's "retry the request" DibRq failure."""
    if isinstance(exc, BridgeError):
        return False
    text = str(exc)
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return True
    if any(marker in text for marker in ("다시", "\xb4\xd9\xbd\xc3")):
        # cp949-decoded "다시 요청하십시오" arriving as latin-1 mojibake
        return True
    args = getattr(exc, "args", ())
    if args and any(value in _TRANSIENT_HRESULTS for value in args if isinstance(value, int)):
        return True
    return False


class BridgeError(Exception):
    """Bridge domain error with a stable code/message/action triple."""

    def __init__(
        self,
        code: str,
        message: str,
        action: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action

    def as_dict(self) -> Dict[str, str]:
        d = {"code": self.code, "message": self.message}
        if self.action:
            d["action"] = self.action
        return d

    def __str__(self) -> str:  # keep tracebacks readable
        if self.action:
            return "{0} [{1}] ({2})".format(self.message, self.code, self.action)
        return "{0} [{1}]".format(self.message, self.code)


def _num(value) -> int:
    """Best-effort cast a COM value to int (some columns are char codes)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return 0


class CybosPlus:
    """A process-wide CybosPlus session.

    * ``factory`` may be injected for tests.  It receives a progid and must
      return an object exposing the Cybos COM surface.
    * Blocking requests run on one serialised background STA thread so
      ``win32com`` objects are always touched from a single apartment.
    * Realtime objects (created through :meth:`create`) are expected to be
      created from the realtime pump thread; :meth:`create` is thread safe
      only because the caller guarantees the correct apartment.
    """

    def __init__(self, factory: Optional[Callable[[str], object]] = None) -> None:
        self._factory = factory
        self._lock = threading.RLock()
        self._queue: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._com_active = False

    # ------------------------------------------------------------------ #
    # Factory plumbing
    # ------------------------------------------------------------------ #
    def create(self, name: str) -> object:
        """Create a COM object for the symbolic dispatch *name*.

        Uses the injected factory when available, otherwise a default factory
        that lazily imports ``win32com.client``.  Must be called from a
        thread that already initialised COM (worker or realtime pump).
        """
        try:
            progid = DISPATCH_NAMES[name]
        except KeyError:
            raise BridgeError(
                "unknown_dispatch", "no such dispatch name: {0!r}".format(name)
            )
        if self._factory is not None:
            return self._factory(progid)
        return self._default_factory(progid)

    def _default_factory(self, progid: str) -> object:
        try:
            import win32com.client  # noqa: PLC0415 - lazy, see module docstring
        except ImportError:
            raise BridgeError(
                "com_unavailable",
                "pywin32 (win32com) is not available in this Python",
                action=_ACTION_INSTALL_PYWIN32,
            )
        return win32com.client.Dispatch(progid)

    # ------------------------------------------------------------------ #
    # Serialised blocking executor (single STA worker thread)
    # ------------------------------------------------------------------ #
    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_main, name="cybos-block-worker", daemon=True
            )
            self._thread.start()

    def _worker_main(self) -> None:
        self._com_initialize()
        try:
            while True:
                try:
                    job = self._queue.get(timeout=_IDLE_POLL_SECONDS)
                except queue.Empty:
                    self.pump_waiting_messages()
                    continue
                if job is None:
                    break
                job()
        finally:
            self._com_uninitialize()
            self._com_active = False

    def pump_waiting_messages(self) -> int:
        """Dispatch pending COM messages on the calling thread; return the count.

        CybosPlus realtime events (``DsCbo1.StockCur`` / ``CpConclusion``) are
        delivered through a COM message window that is owned by the apartment
        doing the Cybos work.  When a blocking request runs *before* a
        subscription exists, that window belongs to this worker thread's
        apartment -- so unless this worker pumps, every later subscription is
        registered successfully and then silently receives nothing.

        Verified live 2026-09-10 (Creon PLUS, 모의투자): subscribing after any
        ``/health`` or ``/quotes`` call produced **0** realtime events until
        this idle pump was added; with it, ticks arrived immediately
        (~40 ticks / 20 s on 005930).  Subscribing as the process's very first
        Cybos call happens to work without pumping, which is why the original
        unit tests and the first smoke test did not catch it.

        Safe no-op when ``pythoncom`` is unavailable (Linux/CI).
        """
        try:
            import pythoncom  # noqa: PLC0415 - lazy, see module docstring
        except ImportError:
            return 0
        dispatched = 0
        try:
            while dispatched < _MAX_IDLE_PUMP and pythoncom.PumpWaitingMessages():
                dispatched += 1
        except Exception:  # noqa: BLE001 - a pump error must not kill the worker
            pass
        return dispatched

    def invoke(self, func: Callable[["CybosPlus"], object]) -> object:
        """Run *func* on the serialised worker thread and return its result.

        ``func`` receives this session and typically calls :meth:`create` /
        :meth:`block_once`.  Exceptions raised by *func* propagate to the
        caller.
        """
        self._ensure_worker()
        box: Dict[str, object] = {}
        done = threading.Event()

        def job() -> None:
            try:
                box["result"] = func(self)
            except BaseException as exc:  # noqa: BLE001 - forwarded verbatim
                box["error"] = exc
            finally:
                done.set()

        self._queue.put(job)
        done.wait()
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        return box.get("result")

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the blocking worker thread (used at shutdown)."""
        thread = None
        with self._lock:
            if self._thread is not None:
                thread = self._thread
                self._thread = None
        if thread is not None and thread.is_alive():
            self._queue.put(None)
            thread.join(timeout)

    def _com_initialize(self) -> None:
        try:
            import pythoncom  # noqa: PLC0415
        except ImportError:
            return
        try:
            pythoncom.CoInitialize()
            self._com_active = True
        except Exception:  # noqa: BLE001 - best effort COM init
            self._com_active = False

    def _com_uninitialize(self) -> None:
        if not self._com_active:
            return
        try:
            import pythoncom  # noqa: PLC0415
        except ImportError:
            return
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001 - best effort
            pass

    # ------------------------------------------------------------------ #
    # Connection helpers
    # ------------------------------------------------------------------ #
    def is_connected(self) -> bool:
        """Return True when PLUS (CybosPlus) is connected.

        Never raises: when COM is unavailable or the link is down this
        returns False so ``/health`` can still answer ``ok: true``.
        """
        try:
            result = self.invoke(
                lambda p: bool(_num(p.create("cybos").IsConnect) != 0)
            )
            return bool(result)
        except BridgeError:
            return False

    def require_connected(self) -> None:
        """Raise :class:`BridgeError` when the PLUS connection is absent.

        Runs synchronously in the calling thread by creating a ``CpCybos``
        probe object there, so it is safe to call from inside a worker job
        (no nested :meth:`invoke`, which would deadlock).  Callers outside a
        worker job should use :meth:`is_connected`.
        """
        ok = False
        try:
            ok = bool(_num(self.create("cybos").IsConnect) != 0)
        except BridgeError:
            ok = False
        if not ok:
            raise BridgeError(
                "not_connected",
                "CybosPlus is not connected (start Creon PLUS and re-login)",
                action="start_plus_and_retry",
            )

    def trade_init(self) -> Tuple[str, str]:
        """Initialise trading and return ``(account_number, goods_flag)``.

        Mirrors the sample pattern ``TradeInit(0)`` then
        ``AccountNumber[0]`` / ``GoodsList(acc, 1)[0]``.
        """
        return self.invoke(lambda p: p._load_account())  # type: ignore[return-value]
    def _load_account(self) -> Tuple[str, str]:
        util = self.create("td_util")
        init = _num(util.TradeInit(0))
        if init != 0:
            raise BridgeError(
                "trade_init_failed",
                "TradeInit(0) failed with code {0}".format(init),
                action="check_trading_credentials_or_market_hours",
            )
        account = util.AccountNumber[0]
        goods = util.GoodsList(account, 1)[0]
        return str(account), str(goods)

    # ------------------------------------------------------------------ #
    # BlockRequest helpers (always executed on the worker thread)
    # ------------------------------------------------------------------ #
    def block_once(
        self,
        name: str,
        configure: Optional[Callable[[object], None]] = None,
        ok_returns: Tuple[int, ...] = (_RET_OK,),
    ) -> object:
        """Create *name*, optionally configure it, and run one BlockRequest.

        Handles the Cybos ``4`` (continuous comms limit) retry loop and raises on
        non-OK return values or a non-zero ``GetDibStatus()``.
        """
        obj = self.create(name)
        if configure is not None:
            configure(obj)
        self.block_until_ok(obj, ok_returns=ok_returns)
        return obj

    def block_again(
        self, obj: object, ok_returns: Tuple[int, ...] = (_RET_OK,)
    ) -> None:
        """Run another BlockRequest on an existing paged query object."""
        self.block_until_ok(obj, ok_returns=ok_returns)

    def block_until_ok(
        self,
        obj: object,
        ok_returns: Tuple[int, ...] = (_RET_OK,),
        max_retries: Optional[int] = None,
    ) -> None:
        retries = 0
        transient = 0
        limit = max_retries if max_retries is not None else 6
        while True:
            try:
                ret = _num(obj.BlockRequest())
            except Exception as exc:  # noqa: BLE001 - COM-level transient
                # CybosPlus raises (HRESULT -2147352567) "DibRq 통신요청
                # 실패하였습니다. 다시 요청하십시오." when its internal request
                # never went out.  Left unhandled, *every* account query fails
                # until the condition clears (observed live: /positions and
                # /balance returned server_error for minutes).  The broker
                # itself says "request again", so retry a few times and then
                # give up loudly.
                if not _is_transient_com_error(exc) or transient >= _TRANSIENT_RETRIES:
                    raise
                transient += 1
                time.sleep(min(0.3 * (2 ** (transient - 1)), 1.5))
                continue
            if ret == _RET_LIMIT and retries < limit:
                wait_ms = _num(self._limit_remain_ms())
                if wait_ms <= 0:
                    wait_ms = 1000
                time.sleep(wait_ms / 1000.0)
                retries += 1
                continue
            break
        if ret not in ok_returns:
            raise BridgeError(
                "request_failed",
                "BlockRequest returned {0}: {1}".format(ret, self._dib_msg(obj)),
                action=_ACTION_RETRY_LATER,
            )
        self.raise_for_dib(obj)

    def raise_for_dib(self, obj: object) -> None:
        """Raise when the last request carried a non-zero GetDibStatus()."""
        status = _num(self._get(obj, "GetDibStatus"))
        if status != 0:
            raise BridgeError(
                "remote_error",
                "remote error {0}: {1}".format(status, self._dib_msg(obj)),
                action=_ACTION_CHECK_REMOTE,
            )

    def _limit_remain_ms(self) -> int:
        try:
            cybos = self.create("cybos")
            return _num(getattr(cybos, "LimitRequestRemainTime", 0))
        except BridgeError:
            return 0

    # Request types for GetLimitRemainCount (per the Daishin "시세 연속 조회
    # 제한 확인" sample creon_algorism3__12): 0 = order, 1 = quote, 2 = realtime.
    REQ_TYPE_ORDER = 0
    REQ_TYPE_QUOTE = 1
    REQ_TYPE_LABELS = {REQ_TYPE_ORDER: "order", REQ_TYPE_QUOTE: "quote"}

    def wait_for_request_slot(
        self,
        req_type: int = REQ_TYPE_ORDER,
        timeout: float = 30.0,
        min_sleep_ms: int = 100,
    ) -> None:
        """Wait until CybosPlus grants a request slot of *req_type*.

        Daishin enforces per-second request budgets; hammering past the budget
        gets the client blocked.  ``CpCybos.GetLimitRemainCount(type)`` returns
        the remaining requests of that type and ``LimitRequestRemainTime`` the
        milliseconds until the budget resets — the same mechanism the official
        rate-limit sample uses (``creon_algorism3__12__시세_연속_조회_제한...``).
        Order/cancel paths call this with ``req_type=0`` before every
        ``BlockRequest`` so bursts (multi-buy, close-all liquidation) are paced
        to the broker budget instead of tripping it.

        Callers without counter support (old stubs, tests) are passed through:
        the ``BlockRequest`` ``ret == 4`` retry loop remains the backstop.
        Raises :class:`BridgeError` ``rate_limited`` when no slot frees within
        *timeout* seconds.
        """
        try:
            cybos = self.create("cybos")
            count = _num(cybos.GetLimitRemainCount(req_type))
            remain_ms = _num(getattr(cybos, "LimitRequestRemainTime", 0))
        except (AttributeError, TypeError, BridgeError):
            return  # no counter surface: rely on the ret == 4 backstop
        deadline = time.time() + max(0.0, timeout)
        while count <= 0:
            wait_s = max(remain_ms, min_sleep_ms) / 1000.0
            if time.time() + wait_s > deadline:
                raise BridgeError(
                    "rate_limited",
                    "no {0} request slot within {1:.0f}s "
                    "(LimitRequestRemainTime={2}ms)".format(
                        self.REQ_TYPE_LABELS.get(req_type, str(req_type)),
                        max(0.0, timeout),
                        remain_ms,
                    ),
                    action=_ACTION_RETRY_LATER,
                )
            time.sleep(wait_s)
            try:
                count = _num(cybos.GetLimitRemainCount(req_type))
                remain_ms = _num(getattr(cybos, "LimitRequestRemainTime", 0))
            except (AttributeError, TypeError):
                return

    @staticmethod
    def _dib_msg(obj: object) -> str:
        try:
            msg = obj.GetDibMsg1()
            return "" if msg is None else str(msg)
        except Exception:  # noqa: BLE001 - fake/COM object may not implement it
            return ""

    @staticmethod
    def _get(obj: object, method: str) -> object:
        return getattr(obj, method)()
