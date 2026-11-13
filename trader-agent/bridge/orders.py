"""Order, cancel and account-query operations.

Every function here returns plain dictionaries/lists and raises
:class:`bridge.plus.BridgeError` on failure.  COM objects are only ever
touched from the serialised worker thread of the :class:`CybosPlus` session
(see :meth:`CybosPlus.invoke`).

Input/output index tables are derived from the Daishin samples under
``creon_extracted_v1/`` (filenames referenced inline and collected in
``bridge/api_notes.md``); nothing is guessed.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from .codes import to_plain_code, to_trade_code
from .plus import BridgeError, CybosPlus

BUY = "buy"
SELL = "sell"
LIMIT = "limit"
MARKET = "market"

# CpTd0311 input(0): buy2 / sell1
_SIDE_CODE = {SELL: "1", BUY: "2"}
# CpTd0311 input(8) order price-type code: 01 regular(limit) / 03 market (per IOC/FOK sample)
_HOGA_CODE = {LIMIT: "01", MARKET: "03"}
# CpTd0311 input(7) order condition type: 0 basic / 1 IOC / 2 FOK (bridge only uses basic)
_ORDER_CONDITION = "0"

_OK_ONLY = (0,)
_PAGED_OK_RETURNS = (0, 1)  # continuous query objects return 0 (and rarely 1) as OK


def default_session() -> CybosPlus:
    """Return the process-wide default :class:`CybosPlus` session."""
    global _DEFAULT_SESSION
    if _DEFAULT_SESSION is None:
        _DEFAULT_SESSION = CybosPlus()
    return _DEFAULT_SESSION


_DEFAULT_SESSION: Optional[CybosPlus] = None


def _s(plus: Optional[CybosPlus]) -> CybosPlus:
    return plus if plus is not None else default_session()


def _num(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return 0


def _clean_code(value) -> str:
    """Best-effort plain 6-digit code from a COM value, never raising."""
    return to_plain_code(str(value).strip())


def _h(obj: object, index: int) -> object:
    return obj.GetHeaderValue(index)


def _d(obj: object, col: int, row: int) -> object:
    return obj.GetDataValue(col, row)


def _trade_job(
    plus: CybosPlus,
    key: str,
    configure: Callable[[object, str, str], None],
    ok_returns: Tuple[int, ...] = _OK_ONLY,
) -> object:
    """Run one blocking request that needs the trading account preloaded.

    ``configure`` receives ``(obj, account, goods_flag)`` and sets inputs.
    """

    def job(p: CybosPlus) -> object:
        p.require_connected()
        account, goods = p._load_account()
        # Pace order/cancel requests to Daishin's per-second order budget so
        # bursts never trip the broker-side block (GetLimitRemainCount(0)).
        p.wait_for_request_slot(CybosPlus.REQ_TYPE_ORDER)
        return p.block_once(
            key, lambda obj: configure(obj, account, goods), ok_returns=ok_returns
        )

    return plus.invoke(job)


# ---------------------------------------------------------------------- #
# New orders
# ---------------------------------------------------------------------- #
def place_order(
    plus: Optional[CybosPlus],
    side: str,
    code: str,
    qty: int,
    price: Optional[int] = None,
    order_type: str = LIMIT,
) -> Dict[str, object]:
    """Place a new stock order (CpTd0311).

    :param side: ``"buy"`` or ``"sell"``.
    :param code: 6-digit stock code, e.g. ``"005930"``.
    :param qty: number of shares.
    :param price: limit price in KRW (required for ``"limit"``, ignored for
        ``"market"`` where the order is sent at market with price ``0``).
    :param order_type: ``"limit"`` (quote type 01) or ``"market"`` (03).
    """
    plus = _s(plus)
    side_k = str(side).lower()
    type_k = str(order_type).lower()
    if side_k not in _SIDE_CODE:
        raise BridgeError("invalid_input", "side must be 'buy' or 'sell'")
    if type_k not in _HOGA_CODE:
        raise BridgeError("invalid_input", "order_type must be 'limit' or 'market'")
    try:
        trade_code = to_trade_code(code)
    except ValueError:
        raise BridgeError("invalid_input", "code must be a 6 digit stock code")
    qty_i = _num(qty)
    if qty_i <= 0:
        raise BridgeError("invalid_input", "qty must be a positive integer")
    ask_price = 0
    if type_k == LIMIT:
        ask_price = _num(price)
        if ask_price <= 0:
            raise BridgeError(
                "invalid_input", "price is required and must be > 0 for limit orders"
            )

    def configure(obj: object, account: str, goods: str) -> None:
        obj.SetInputValue(0, _SIDE_CODE[side_k])  # buy2 / sell1
        obj.SetInputValue(1, account)             # account no
        obj.SetInputValue(2, goods)               # goods type (first stock goods)
        obj.SetInputValue(3, trade_code)          # stock code (A-prefixed)
        obj.SetInputValue(4, qty_i)               # order qty
        obj.SetInputValue(5, ask_price)           # order price (0 for market)
        obj.SetInputValue(7, _ORDER_CONDITION)    # order condition type 0: basic
        obj.SetInputValue(8, _HOGA_CODE[type_k])  # quote type 01 regular / 03 market

    obj = _trade_job(plus, "order", configure)
    order_no = _num(_h(obj, 7))  # CpTd0311/0313 output header(7) is the new order no
    return {
        "order_no": order_no,
        "side": side_k,
        "code": to_plain_code(trade_code),
        "qty": qty_i,
        "price": ask_price,
        "order_type": type_k,
        "message": plus._dib_msg(obj),
    }


# ---------------------------------------------------------------------- #
# Cancel
# ---------------------------------------------------------------------- #
def cancel_order(
    plus: Optional[CybosPlus],
    order_no: str,
    code: str,
    qty: Optional[int] = None,
) -> Dict[str, object]:
    """Cancel an outstanding order (CpTd0314).

    ``qty`` defaults to ``0`` which cancels the whole remaining quantity
    (remaining cancel) exactly as the samples do.
    """
    plus = _s(plus)
    try:
        trade_code = to_trade_code(code)
    except ValueError:
        raise BridgeError("invalid_input", "code must be a 6 digit stock code")
    qty_i = _num(qty) if qty is not None else 0
    order_no_s = str(order_no).strip()

    def configure(obj: object, account: str, goods: str) -> None:
        obj.SetInputValue(1, order_no_s)   # original order no
        obj.SetInputValue(2, account)      # account no
        obj.SetInputValue(3, goods)        # goods type
        obj.SetInputValue(4, trade_code)   # stock code (A-prefixed)
        obj.SetInputValue(5, qty_i)        # amend/cancel qty, 0 => remaining cancel

    obj = _trade_job(plus, "cancel", configure)
    return {
        "order_no": order_no_s,
        "code": to_plain_code(trade_code),
        "qty": qty_i,
        "message": plus._dib_msg(obj),
    }


# ---------------------------------------------------------------------- #
# Outstanding orders (outstanding query, CpTd5339)
# ---------------------------------------------------------------------- #
def fetch_open_orders(plus: Optional[CybosPlus]) -> List[Dict[str, object]]:
    """Return the list of outstanding (unfilled) orders.

    Paged through the CpTd5339 20-row pages exactly like
    ``creon_algorism3__08__stock_outstanding...``.  Codes are stripped to 6 digits.
    """

    def job(p: CybosPlus) -> List[Dict[str, object]]:
        p.require_connected()
        account, goods = p._load_account()

        def configure(obj: object) -> None:
            obj.SetInputValue(0, account)
            obj.SetInputValue(1, goods)
            obj.SetInputValue(4, "0")  # all
            obj.SetInputValue(5, "1")  # sort basis - reverse
            obj.SetInputValue(6, "0")  # all
            obj.SetInputValue(7, 20)   # request count (max 20)

        obj = p.block_once("open_orders", configure, ok_returns=_PAGED_OK_RETURNS)
        result: List[Dict[str, object]] = []
        while True:
            cnt = _num(_h(obj, 5))
            for i in range(cnt):
                result.append(
                    {
                        "order_no": _d(obj, 1, i),
                        "original_order_no": _d(obj, 2, i),
                        "code": _clean_code(_d(obj, 3, i)),
                        "name": _d(obj, 4, i),
                        "description": _d(obj, 5, i),      # order type detail
                        "qty": _d(obj, 6, i),              # order qty
                        "price": _d(obj, 7, i),            # order price
                        "filled_qty": _d(obj, 8, i),       # filled qty
                        "credit": _d(obj, 9, i),           # credit type
                        "cancelable_qty": _d(obj, 11, i),  # amend/cancel available qty
                        "bs_code": _d(obj, 13, i),         # trading type code (1/2)
                        "loan_date": _d(obj, 17, i),       # loan date
                        "order_hoga_desc": _d(obj, 19, i), # quote type detail
                        "order_hoga_code": _d(obj, 21, i), # quote typecode
                    }
                )
            if not getattr(obj, "Continue", False):
                break
            p.block_again(obj, ok_returns=_PAGED_OK_RETURNS)
        return result

    return _s(plus).invoke(job)  # type: ignore[return-value]


# ---------------------------------------------------------------------- #
# Positions / balance (stock balance, CpTd6033)
# ---------------------------------------------------------------------- #
def fetch_positions(plus: Optional[CybosPlus]) -> List[Dict[str, object]]:
    """Return per-position stock balance rows from CpTd6033 (paged to 50).

    Mirrors ``creon_algorism2__04__stock_balance_realtime...`` and
    ``creon_algorism3__15__stock_balance_stock_query...``.
    """

    def job(p: CybosPlus) -> List[Dict[str, object]]:
        p.require_connected()
        account, goods = p._load_account()

        def configure(obj: object) -> None:
            obj.SetInputValue(0, account)
            obj.SetInputValue(1, goods)
            obj.SetInputValue(2, 50)  # request items (max 50)

        obj = p.block_once("positions", configure, ok_returns=_PAGED_OK_RETURNS)
        result: List[Dict[str, object]] = []
        while True:
            cnt = _num(_h(obj, 7))
            for i in range(cnt):
                result.append(
                    {
                        "code": _clean_code(_d(obj, 12, i)),
                        "name": _d(obj, 0, i),
                        "credit_type": _d(obj, 1, i),  # credit type (single char)
                        "loan_date": _d(obj, 2, i),
                        "qty": _d(obj, 7, i),          # executed balance qty
                        "sellable_qty": _d(obj, 15, i),  # sellable qty
                        "avg_price": _d(obj, 17, i),   # executed book price
                        "eval_amount": _d(obj, 9, i),  # eval amount
                        "eval_pnl": _d(obj, 11, i),    # eval pnl
                    }
                )
            if not getattr(obj, "Continue", False):
                break
            p.block_again(obj, ok_returns=_PAGED_OK_RETURNS)
        return result

    return _s(plus).invoke(job)  # type: ignore[return-value]


def fetch_balance(plus: Optional[CybosPlus]) -> Dict[str, object]:
    """Return an aggregated account balance summary.

    The summary is computed from the CpTd6033 position rows (same request as
    positions) and enriched with the official header fields (verified against
    the Daishin helper doc "[계좌별 잔고 평가현황] CpTrade.CpTd6033") plus a
    dedicated buyable-cash query (CpTdNew5331A, "[계좌별 매수 가능금액/수량]"):

    * ``total_buy_amount`` = sum(executed book price x executed balance qty) - matches the
      samples' own "buy amount" computation.
    * ``total_eval_amount`` = sum(eval amount), ``total_eval_pnl`` = sum(eval pnl).
    * ``equity`` = CpTd6033 header(3) total evaluation (예수금+대주+잔고 평가) when
      non-zero, else falls back to ``cash + total_eval_amount``.
    * ``d2_deposit`` = CpTd6033 header(9) D+2 예상예수금; ``loan_amount`` = header(6).
    * ``cash`` = CpTdNew5331A header(10) 현금주문가능금액 (the amount usable for a
      cash buy order).  ``deposit`` = header(45) 예수금, ``usable_deposit`` =
      header(47) 가능예수금.  When the buyable query fails (e.g. unsupported on
      the simulation server) ``cash`` falls back to ``d2_deposit`` and
      ``buyable_error`` carries the reason; a fully unavailable account query
      leaves ``cash`` unset so the 64-bit core fails closed (no cash => no buys).
    """

    def buyable_inputs(account: str, goods: str) -> Callable[[object], None]:
        def configure(obj: object) -> None:
            obj.SetInputValue(0, account)             # account no
            obj.SetInputValue(1, goods)               # goods type
            obj.SetInputValue(2, "")                  # stock code: "" = amount query
            obj.SetInputValue(3, "01")                # quote type: regular/limit
            obj.SetInputValue(6, "1")                 # 조회구분: 1 = amount query
        return configure

    def job(p: CybosPlus) -> Dict[str, object]:
        p.require_connected()
        account, goods = p._load_account()

        def configure(obj: object) -> None:
            obj.SetInputValue(0, account)
            obj.SetInputValue(1, goods)
            obj.SetInputValue(2, 50)

        obj = p.block_once("positions", configure, ok_returns=_PAGED_OK_RETURNS)
        total_buy = 0
        total_eval = 0
        total_pnl = 0
        count = 0
        while True:
            cnt = _num(_h(obj, 7))
            for i in range(cnt):
                qty = _num(_d(obj, 7, i))
                avg = _num(_d(obj, 17, i))
                total_buy += avg * qty
                total_eval += _num(_d(obj, 9, i))
                total_pnl += _num(_d(obj, 11, i))
                count += 1
            if not getattr(obj, "Continue", False):
                break
            p.block_again(obj, ok_returns=_PAGED_OK_RETURNS)

        # CpTd6033 official header fields (helper doc seq=176).
        header_total_eval = _num(_h(obj, 3))   # 총평가금액 (helper doc)
        d2_deposit = _num(_h(obj, 9))          # D+2 예상예수금
        loan_amount = _num(_h(obj, 6))         # 대출금액

        # CpTdNew5331A buyable cash (helper doc seq=171).  Never let a failure
        # kill the whole balance: degrade to d2_deposit and note the error.
        cash: Optional[int] = None
        deposit: Optional[int] = None
        usable_deposit: Optional[int] = None
        buyable_error = ""
        try:
            buyable = p.block_once("buyable", buyable_inputs(account, goods))
            cash = _num(_h(buyable, 10))   # 현금주문가능금액
            deposit = _num(_h(buyable, 45))  # 예수금
            usable_deposit = _num(_h(buyable, 47))  # 가능예수금
        except BridgeError as exc:
            buyable_error = exc.message

        if cash is None or cash == 0:
            # 0 is not a usable cash figure either (empty account vs failed
            # query are indistinguishable here); prefer the D+2 estimate.
            cash = d2_deposit if d2_deposit > 0 else cash

        # Equity = orderable cash + stock evaluation.  Verified live 2026-09-10
        # on 모의투자: with 5 positions filled, CpTd6033 header(3) returned
        # 598,290 (= exactly the stock evaluation) while cash was 4,401,227 --
        # i.e. the header is NOT total assets despite the helper doc wording.
        # Using it as equity made the 64-bit core see a ~88 % daily loss, which
        # would have tripped R4 (liquidate-all + halt).  Cash + stock eval is
        # the value the risk gates and Kelly sizing must use; the raw header
        # stays available as ``broker_total_eval`` for diagnostics.
        equity = (cash or 0) + total_eval

        result: Dict[str, object] = {
            "account": account,
            "goods_flag": goods,
            "positions_count": count,
            "total_buy_amount": total_buy,
            "total_eval_amount": total_eval,
            "total_eval_pnl": total_pnl,
            "equity": equity,
            "d2_deposit": d2_deposit,
            "loan_amount": loan_amount,
        }
        if header_total_eval > 0:
            result["broker_total_eval"] = header_total_eval
        if cash is not None:
            result["cash"] = cash
            result["withdrawable"] = usable_deposit if usable_deposit is not None else cash
        if deposit is not None:
            result["deposit"] = deposit
            result["usable_deposit"] = usable_deposit
        if buyable_error:
            result["buyable_error"] = buyable_error
        return result

    return _s(plus).invoke(job)  # type: ignore[return-value]


# ---------------------------------------------------------------------- #
# Daily P&L (executed-basis daily trade pnl, CpTd6032)
# ---------------------------------------------------------------------- #
def fetch_daily_pnl(plus: Optional[CybosPlus]) -> Dict[str, object]:
    """Return executed-basis daily P&L from CpTd6032.

    Follows ``creon_algorism2__02__executed-basis_stock_daily_trade_pnl_example.py``.
    Header rows 1/2/3 are the summary (balance eval pnl / sell realized pnl /
    return rate); rows carry per-stock detail.
    """

    def job(p: CybosPlus) -> Dict[str, object]:
        p.require_connected()
        account, goods = p._load_account()

        def configure(obj: object) -> None:
            obj.SetInputValue(0, account)
            obj.SetInputValue(1, goods)

        obj = p.block_once("daily_pnl", configure, ok_returns=_PAGED_OK_RETURNS)
        summary: Dict[str, object] = {}
        items: List[Dict[str, object]] = []
        first_page = True
        while True:
            cnt = _num(_h(obj, 0))
            if first_page:
                summary = {
                    "eval_pnl": _h(obj, 1),      # balance eval pnl (sumJango)
                    "realized_pnl": _h(obj, 2),  # sell realized pnl (sumSellM)
                    "return_rate": _h(obj, 3),   # return rate (sumRate)
                }
                first_page = False
            for i in range(cnt):
                items.append(
                    {
                        "code": _clean_code(_d(obj, 12, i)),
                        "name": _d(obj, 0, i),
                        "loan_date": _d(obj, 1, i),
                        "prev_balance_qty": _d(obj, 2, i),
                        "buy_qty": _d(obj, 3, i),
                        "sell_qty": _d(obj, 4, i),
                        "today_balance_qty": _d(obj, 5, i),
                        "avg_buy_price": _d(obj, 6, i),
                        "avg_sell_price": _d(obj, 7, i),
                        "current_price": _d(obj, 8, i),
                        "eval_pnl": _d(obj, 9, i),
                        "realized_pnl": _d(obj, 10, i),
                        "return_rate": _d(obj, 11, i),
                    }
                )
            if not getattr(obj, "Continue", False):
                break
            p.block_again(obj, ok_returns=_PAGED_OK_RETURNS)
        return {"summary": summary, "items": items}

    return _s(plus).invoke(job)  # type: ignore[return-value]


# ---------------------------------------------------------------------- #
# Quote snapshots (DsCbo1.StockMst)
# ---------------------------------------------------------------------- #
def fetch_quotes(
    plus: Optional[CybosPlus], codes: List[str]
) -> Dict[str, Dict[str, object]]:
    """Fetch current snapshots for *codes* via ``DsCbo1.StockMst``.

    One BlockRequest per code (the samples always use a per-code StockMst);
    quote objects are serialised on the worker thread.  Header mapping from
    ``creon_algorism3__23__stock_current_query_realtime.py`` and the IOC/FOK
    sample.

    Request budget: each snapshot is a **quote** request, and Daishin blocks a
    client that exceeds its per-second quote budget, so every call is paced
    with ``GetLimitRemainCount(1)`` (``REQ_TYPE_QUOTE``) first.  Duplicate
    codes are collapsed before the loop because every extra code costs a real
    broker request.
    """

    def job(p: CybosPlus) -> Dict[str, Dict[str, object]]:
        p.require_connected()
        result: Dict[str, Dict[str, object]] = {}
        seen = set()
        for raw in codes:
            try:
                trade_code = to_trade_code(raw)
            except ValueError:
                raise BridgeError(
                    "invalid_input", "code must be a 6 digit stock code"
                )
            plain = to_plain_code(trade_code)
            if plain in seen:
                continue
            seen.add(plain)

            def configure(obj: object, _tc: str = trade_code) -> None:
                obj.SetInputValue(0, _tc)

            p.wait_for_request_slot(CybosPlus.REQ_TYPE_QUOTE)
            obj = p.block_once("stock_mst", configure)
            result[plain] = {
                "code": _clean_code(_h(obj, 0)),
                "name": _h(obj, 1),
                "time": _h(obj, 4),
                "current": _h(obj, 11),   # close / current
                "diff": _h(obj, 12),      # prev change
                "open": _h(obj, 13),
                "high": _h(obj, 14),
                "low": _h(obj, 15),
                "offer1": _h(obj, 16),    # sell price 1
                "bid1": _h(obj, 17),      # buy price 1
                "volume": _h(obj, 18),
                "amount": _h(obj, 19),    # turnover
                "base": _h(obj, 27),      # base price
            }
        return result

    return _s(plus).invoke(job)  # type: ignore[return-value]
