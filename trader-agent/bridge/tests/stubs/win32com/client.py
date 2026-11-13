"""Fake ``win32com.client`` module.

Provides ``Dispatch(progid)`` backed by a registry of :class:`FakeCom`
objects.  The bridge's real CybosPlus default factory does
``import win32com.client; win32com.client.Dispatch(progid)`` lazily, so with
this module installed via ``sys.modules`` the whole bridge runs without
pywin32 and every ``SetInputValue`` / ``BlockRequest`` is captured.
"""

from __future__ import annotations

from typing import Dict

REGISTRY: Dict[str, "FakeCom"] = {}


def reset() -> None:
    """Drop every registered fake object (call between tests)."""
    REGISTRY.clear()


def register(progid: str, obj: "FakeCom") -> "FakeCom":
    """Register *obj* as the fake returned for *progid*."""
    REGISTRY[progid] = obj
    return obj


def make(progid: str) -> "FakeCom":
    """Create, register and return a fresh :class:`FakeCom` for *progid*."""
    obj = FakeCom(progid)
    return register(progid, obj)


def Dispatch(progid: str) -> "FakeCom":
    """Fake ``win32com.client.Dispatch``."""
    obj = REGISTRY.get(progid)
    if obj is None:
        obj = make(progid)
    return obj


def WithEvents(_object, _event_class):
    """Fake ``win32com.client.WithEvents`` (used by the realtime hub only)."""
    return _event_class()


class FakeCom:
    """A controllable double for a CybosPlus COM object.

    Defaults:
      * ``IsConnect = 1``
      * ``AccountNumber = ("999-99-999999",)``
      * ``GoodsList(...)`` returns ``("01",)``
      * ``TradeInit(n)`` returns 0
      * ``BlockRequest()`` returns 0 and ``GetDibStatus()`` returns 0
    """

    def __init__(self, progid: str) -> None:
        self.progid = progid
        self.inputs: Dict[int, object] = {}
        self.block_calls = 0
        self.headers: Dict[int, object] = {}
        self.rows: Dict[int, list] = {}
        self._continue = False
        self.block_ret = 0
        self.dib_status = 0
        self.dib_msg = ""
        # connection / trading defaults
        self.IsConnect = 1
        self.AccountNumber = ("999-99-999999",)
        self.LimitRequestRemainTime = 0
        self.limit_remain_count = 10  # GetLimitRemainCount default (>=1: slot free)
        self.goods = ("01",)
        self.subscribed = False

    # -- CpCybos request-budget surface -----------------------------------
    def GetLimitRemainCount(self, _req_type: int) -> int:
        return self.limit_remain_count

    # -- CpTdUtil surface ------------------------------------------------
    def GoodsList(self, _account: str, _kind: int):
        return self.goods

    def TradeInit(self, _n: int) -> int:
        return 0

    # -- input/output capture --------------------------------------------
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

    # -- realtime surface (subscribe/unsubscribe) ------------------------
    def Subscribe(self) -> None:
        self.subscribed = True

    def Unsubscribe(self) -> None:
        self.subscribed = False

    @property
    def Continue(self) -> bool:
        return self._continue
