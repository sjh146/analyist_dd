"""Reconcile the trade journal against the broker's actual positions.

The journal is the system's memory, so when it disagrees with the broker the
system's view of its own risk is wrong.  Disagreements happen in practice:

* a market sell is *journaled* as closed but never filled (or was rejected),
* an exit is journaled at a bogus price (the ``price: 0`` market-order echo,
  which booked a whole position as a loss on 2026-09-10),
* an accepted-but-unfilled order is journaled as a position although the broker
  only holds it as a working order,
* a position exists at the broker that the journal never recorded.

Rules (never invented data -- every price comes from the broker, from the
journal, or from an explicitly supplied ``--fix-exit``/``--exit-price``):

1. **journal open, broker flat** -> close it (needs a price; else reported).
2. **journal closed, broker holds it** -> reopen (the "close" was a phantom).
3. **broker holds it, journal has no open row** -> adopt at the broker's own
   ``avg_price``/``qty``.
4. **journal closed at a non-positive price** -> the exit price is unknown;
   correct it only when a trustworthy price is supplied.
5. **journal open, broker has an unfilled order for it** -> close at the entry
   price with zero PnL (it never became a position).

Everything is a report until ``apply=True``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _plain(code: Any) -> Optional[str]:
    text = str(code or "").strip().upper()
    if text.startswith("A") and len(text) == 7 and text[1:].isdigit():
        text = text[1:]
    return text if (len(text) == 6 and text.isdigit()) else None


def _broker_positions(positions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Return ``{plain_code: position}`` for a bridge ``/positions`` list."""
    result: Dict[str, Dict[str, Any]] = {}
    for position in positions or []:
        code = _plain(position.get("code"))
        if code:
            result[code] = position
    return result


def _unfilled_orders(orders: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Return ``{plain_code: order}`` for orders that **failed to be accepted** (rejected).

    2026-10-06 실사고 수리: 예전에는 `filled_qty <= 0` 인 주문을 전부 "미체결"로 보고 원가 청산했다.
    그런데 체결 0 이어도 **취소 가능 수량이 남은 계류(resting) 주문**은 나중에 실제로 체결된다
    (319400 실측: 계류 → 정정 뒤 체결 → 저널/브로커가 반대로 어긋나 재정합을 한 번 더 돌렸다).
    그래서 `reconcile_rules.order_state` 가 **'rejected'** 로 판정한 주문만 정리 대상으로 남긴다.
    계류·판정불가는 **정정하지 않는다**(다음 정합에서 결말을 다시 본다).
    """
    from trader_core.reconcile_rules import order_state
    result: Dict[str, Dict[str, Any]] = {}
    for order in orders or []:
        code = _plain(order.get("code"))
        if not code:
            continue
        state = order_state(order.get("filled_qty"), order.get("cancelable_qty"),
                            order.get("order_no"))
        if state == "rejected":
            result[code] = order
    return result


def reconcile(
    journal: Any,
    broker_positions: List[Dict[str, Any]],
    quotes: Optional[Dict[str, Dict[str, Any]]] = None,
    open_orders: Optional[List[Dict[str, Any]]] = None,
    exit_price: Optional[float] = None,
    exit_prices: Optional[Dict[str, float]] = None,
    apply: bool = False,
    apply_flat_close: bool = True,
) -> Dict[str, Any]:
    """Return the reconciliation report (and apply it when ``apply``).

    ``apply_flat_close=False`` keeps rule 1 (journal open, broker flat) as a
    *report only* even with ``apply=True``: closing it needs a fill price, and
    the live quote is not a fill price.  Callers that cannot obtain the broker's
    own fill (the runner's post-close pass) use this to repair the cases where
    the broker is the authority while refusing to invent a P&L.
    """
    quotes = quotes or {}
    exit_prices = exit_prices or {}
    held = _broker_positions(broker_positions)
    unfilled = _unfilled_orders(open_orders or [])
    journal_open = {row["code"]: row for row in journal.open_trades()}
    closed_rows = journal.closed_trades(period_days=3650)
    known_open = set(journal_open)

    reopened: List[Dict[str, Any]] = []
    closed: List[Dict[str, Any]] = []
    adopted: List[Dict[str, Any]] = []
    corrected: List[Dict[str, Any]] = []
    unfilled_rows: List[Dict[str, Any]] = []
    bogus_exits: List[Dict[str, Any]] = []
    needs_price: List[Dict[str, Any]] = []
    qty_fixed: List[Dict[str, Any]] = []
    notes: List[str] = []

    def price_for(code: str) -> Optional[float]:
        if exit_price:
            return float(exit_price)
        if code in exit_prices:
            return float(exit_prices[code])
        quote = quotes.get(code) or {}
        current = _num(quote.get("current") or quote.get("price"))
        return current if current > 0 else None

    # 1) journal says open, broker says flat -> the exit never really filled
    #    (or was journaled at a bogus price); close it at a real price.
    for code, row in sorted(journal_open.items()):
        if code in held:
            # 6) Both sides hold it but the share count disagrees -> the entry
            #    was a partial fill (the order response is not a fill
            #    confirmation).  The broker's fill is the fact: correct the
            #    journal's qty/price, never invent numbers.
            position = held[code]
            broker_qty = _num(position.get("qty"))
            journal_qty = _num(row["qty"])
            broker_avg = _num(position.get("avg_price")) or _num(row["price"])
            if broker_qty > 0 and broker_qty != journal_qty:
                qty_fixed.append(
                    {
                        "code": code,
                        "trade_id": row["id"],
                        "journal_qty": journal_qty,
                        "broker_qty": broker_qty,
                        "journal_price": _num(row["price"]),
                        "broker_price": broker_avg,
                    }
                )
                notes.append(
                    "{0}: journal says {1:.0f} shares but the broker holds "
                    "{2:.0f} (partial fill) -> entry corrected to the broker's "
                    "fill at {3:,.2f}".format(
                        code, journal_qty, broker_qty, broker_avg
                    )
                )
                if apply:
                    journal.adjust_entry(
                        row["id"],
                        qty=broker_qty,
                        price=broker_avg,
                        note="reconcile: partial fill ({0:.0f} of {1:.0f})".format(
                            broker_qty, journal_qty
                        ),
                    )
            continue
        if code in unfilled:
            # 5) it never became a position: an unfilled working order exists.
            unfilled_rows.append(
                {"code": code, "trade_id": row["id"], "qty": _num(row["qty"]),
                 "entry": _num(row["price"]), "order_no": unfilled[code].get("order_no")}
            )
            notes.append(
                "{0}: journal has it as a position but the broker order {1} is "
                "unfilled (filled_qty=0) -> closed at cost, zero PnL".format(
                    code, unfilled[code].get("order_no")
                )
            )
            if apply:
                journal.close_trade(
                    trade_id=row["id"],
                    exit_price=_num(row["price"]),
                    exit_reason="reconcile: order never filled",
                )
            known_open.discard(code)
            continue
        price = price_for(code)
        if price is None:
            needs_price.append({"code": code, "trade_id": row["id"], "qty": row["qty"]})
            notes.append(
                "{0}: broker is flat but no price is available to close it "
                "(pass --exit-price or restore the bridge)".format(code)
            )
            continue
        entry = _num(row["price"])
        qty = _num(row["qty"])
        closed.append(
            {"code": code, "trade_id": row["id"], "qty": qty, "entry": entry,
             "exit": price, "pnl": (price - entry) * qty}
        )
        if apply and apply_flat_close:
            journal.close_trade(
                trade_id=row["id"], exit_price=price,
                exit_reason="reconcile: broker flat",
            )
        known_open.discard(code)

    # 2) journal closed, broker still holds -> reopen (phantom close).
    for row in sorted(closed_rows, key=lambda r: r["ts"]):
        code = row["code"]
        if code not in held or code in known_open:
            continue
        reopened.append(
            {"code": code, "trade_id": row["id"], "qty": _num(row["qty"]),
             "was_exit_price": _num(row["exit_price"]), "pnl": _num(row["pnl"])}
        )
        notes.append(
            "{0}: journal closed it at {1:,.0f} but the broker still holds "
            "{2:.0f} shares -> reopened".format(
                code, _num(row["exit_price"]), _num(row["qty"])
            )
        )
        if apply:
            journal.reopen_trade(row["id"])
        known_open.add(code)

    # 3) broker holds it, journal has no open row -> adopt at broker cost.
    for code, position in sorted(held.items()):
        if code in known_open:
            continue
        qty = _num(position.get("qty"))
        entry = _num(position.get("avg_price"))
        if qty <= 0 or entry <= 0:
            notes.append("{0}: broker position without qty/avg_price; skipped".format(code))
            continue
        adopted.append({"code": code, "qty": qty, "entry": entry,
                        "name": position.get("name", "")})
        notes.append(
            "{0}: broker holds {1:.0f} shares @ {2:,.0f} that the journal never "
            "recorded -> adopted".format(code, qty, entry)
        )
        if apply:
            journal.record_trade(
                side="buy", code=code, name=str(position.get("name", "") or ""),
                qty=int(qty), price=entry, order_type="reconciled",
                entry_reason="reconcile: adopted from broker",
            )
        known_open.add(code)

    # 4) journal closed at a non-positive price -> price is unknown.
    for row in sorted(closed_rows, key=lambda r: r["ts"]):
        code = row["code"]
        if code in known_open or _num(row["exit_price"]) > 0:
            continue
        price = price_for(code)
        if price is None:
            bogus_exits.append(
                {"code": code, "trade_id": row["id"], "pnl_wrong": _num(row["pnl"]),
                 "qty": _num(row["qty"]), "entry": _num(row["price"])}
            )
            notes.append(
                "{0}: closed with exit_price<=0 (pnl recorded {1:,.0f}) - supply "
                "the real price (--fix-exit {0}=PRICE) to correct it".format(
                    code, _num(row["pnl"])
                )
            )
            continue
        entry = _num(row["price"])
        qty = _num(row["qty"])
        corrected.append(
            {"code": code, "trade_id": row["id"], "qty": qty, "entry": entry,
             "exit": price, "pnl": (price - entry) * qty,
             "pnl_recorded": _num(row["pnl"])}
        )
        if apply:
            journal.correct_exit(
                row["id"], exit_price=price,
                exit_reason="{} (price corrected by reconcile)".format(
                    row.get("exit_reason") or "exit"
                ),
            )

    return {
        "applied": bool(apply),
        "broker_codes": sorted(held),
        "journal_open_codes": sorted(journal_open),
        "reopened": reopened,
        "closed": closed,
        "adopted": adopted,
        "corrected": corrected,
        "unfilled": unfilled_rows,
        "bogus_exits": bogus_exits,
        "needs_price": needs_price,
        "qty_fixed": qty_fixed,
        "notes": notes,
    }
