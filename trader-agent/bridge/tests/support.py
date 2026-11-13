"""Shared helpers for bridge tests: fake session + registered COM doubles."""

from __future__ import annotations

from typing import Dict, Tuple

from bridge.plus import CybosPlus
from bridge.tests.stubs import install_stub

ACC = "1234567890"
GOODS = ("01",)


def client():
    """Return the fake ``win32com.client`` module (installs stub first).

    Always resolved from ``sys.modules`` so tests and the bridge's lazy
    ``import win32com.client`` (in the blocking worker thread) share one
    registry.
    """
    import sys

    install_stub()
    return sys.modules["win32com.client"]


def standard_fakes() -> Dict[str, object]:
    """Register the standard fakes (cybos/td_util/order/cancel) and return them.

    Registry is reset first so each test starts from a clean slate.
    """
    wc = client()
    wc.reset()
    cybos = wc.make("CpUtil.CpCybos")
    cybos.IsConnect = 1
    cybos.LimitRequestRemainTime = 0

    td = wc.make("CpTrade.CpTdUtil")
    td.AccountNumber = (ACC,)
    td.goods = GOODS

    order = wc.make("CpTrade.CpTd0311")
    cancel = wc.make("CpTrade.CpTd0314")
    return {"cybos": cybos, "td_util": td, "order": order, "cancel": cancel}


def fake_session() -> Tuple[CybosPlus, Dict[str, object]]:
    """Return a default-factory :class:`CybosPlus` over the standard fakes."""
    fakes = standard_fakes()
    return CybosPlus(), fakes
