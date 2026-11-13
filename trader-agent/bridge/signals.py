"""Signal-source queries: ranking and (later) feature-stock / conditional search.

Recipe provenance: ``bridge/SIGNALS_NOTES.md`` §1, verified line-by-line against
``크레온 기본알고리즘/creon_algorism3.txt`` §"[파이썬] 당일 상승률 상위 200 종목
실시간 통신 예제" (class ``Cp7043`` at L5121-L5174).  Every index below is quoted
from that sample, not guessed:

* nine ``SetInputValue`` calls, sent **once** at construction, then only
  ``BlockRequest()`` per page (paging does not re-send inputs);
* the sample **ignores the ``BlockRequest()`` return value** and tests only
  ``GetDibStatus() != 0`` -- so ``ok_returns`` here is ``{0, 1}`` (like the other
  paged queries) while the ``ret == 4`` retry backstop still applies;
* columns are **not dense**: 0=코드 1=종목명 3=대비부호 4=대비 6=거래량;
* ``while obj.Continue`` paging, hard cap 200 rows.

This class is the answer to "where do candidates come from without an external
data feed": HTS's own 상승률 ranking, requested straight from the broker.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .codes import to_plain_code
from .plus import BridgeError, CybosPlus

# ``CpSvrNew7043`` reads its failure state from GetDibStatus() only.
_PAGED_OK_RETURNS = (0, 1)

# idx -> (base value in the sample).  The sample mixes ``ord('x')`` char codes
# with plain ints in the same call site (L5125-L5133), so the two are kept
# distinct here and never normalised against each other.
_SAMPLE_INPUTS: Dict[int, Any] = {
    0: ord("0"),  # 시장: 거래소 + 코스닥
    1: ord("2"),  # 등락 방향: 상승
    2: ord("1"),  # 기간: 당일
    3: 21,        # 정렬: 전일대비 상위 순
    4: ord("1"),  # 관리종목 제외
    5: ord("0"),  # 거래량 필터: 전체
    6: ord("0"),  # 표시 항목: 시가대비
    7: 0,         # 등락율 시작 (%)
    8: 30,        # 등락율 끝 (%)
}

DIRECTIONS = {"up": "2", "down": "1"}
PERIODS = {"day": "1"}


def _num(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return 0


def _plain_stock_code(value) -> Optional[str]:
    """Return a plain 6-digit stock code, or ``None`` for anything else.

    ``bridge.codes.to_plain_code`` deliberately passes non-stock prefixes
    (indices/futures such as ``Q530031``) through unchanged, so the ranking --
    which must yield **stock** candidates only -- filters here instead.
    """
    text = str(value or "").strip()
    if text[:1].upper() == "A":
        text = text[1:]
    if len(text) == 6 and text.isdigit():
        return text
    return None


def rank_inputs(
    direction: str = "up",
    period: str = "day",
    market: str = "0",
    sort: int = 21,
    exclude_managed: bool = True,
    volume_filter: str = "0",
    display: str = "0",
    pct_from: int = 0,
    pct_to: int = 30,
    ord_style: str = "char",
) -> Dict[int, Any]:
    """Return the nine ``SetInputValue`` pairs for a ranking request.

    Defaults reproduce the sample byte-for-byte (``ord_style="char"``).  Two
    knobs exist because the sample never exercises them and they are the
    difference between a useless and a useful scan:

    * ``pct_to`` -- the sample asks for a ``0..30 %`` window, which **excludes
      상한가 (+30 %)** by construction.  Pass a larger bound (e.g. 100) to
      include limit-up names.
    * ``ord_style`` -- ``"char"`` sends ``ord('0')`` like the sample;
      ``"int"`` sends the bare integer ``0`` (the ambiguity flagged in
      ``CREON_ALGORISM_CATALOG.md``).  Exposed for a one-off live A/B test.
    """
    if direction not in DIRECTIONS:
        raise BridgeError("invalid_input", "direction must be 'up' or 'down'")
    if period not in PERIODS:
        raise BridgeError("invalid_input", "period must be 'day'")
    if ord_style not in ("char", "int"):
        raise BridgeError("invalid_input", "ord_style must be 'char' or 'int'")

    def char(digit: str) -> Any:
        return ord(digit) if ord_style == "char" else int(digit)

    return {
        0: char(market),
        1: char(DIRECTIONS[direction]),
        2: char(PERIODS[period]),
        3: int(sort),
        4: char("1") if exclude_managed else char("0"),
        5: char(volume_filter),
        6: char(display),
        7: int(pct_from),
        8: int(pct_to),
    }


def fetch_rank(
    plus: Optional[CybosPlus] = None,
    direction: str = "up",
    period: str = "day",
    market: str = "0",
    sort: int = 21,
    exclude_managed: bool = True,
    volume_filter: str = "0",
    display: str = "0",
    pct_from: int = 0,
    pct_to: int = 30,
    limit: int = 200,
    ord_style: str = "char",
    raw_inputs: Optional[Dict[int, Any]] = None,
) -> Dict[str, Any]:
    """Fetch the ranking list (당일 상승률/하락률 상위).

    Returns ``{"items": [...], "total_count": int, "pages": int}`` where each
    item is ``{code, name, diff_sign, diff, volume}``.  ``limit`` caps the
    collected rows (200 = the sample's own cap).

    Every page costs one **quote** request, so each page is paced with
    ``GetLimitRemainCount(1)`` first.
    """
    from .orders import _s  # local import avoids a cycle at module import

    session = _s(plus)

    def job(p: CybosPlus) -> Dict[str, Any]:
        p.require_connected()
        inputs = rank_inputs(
            direction=direction, period=period, market=market, sort=sort,
            exclude_managed=exclude_managed, volume_filter=volume_filter,
            display=display, pct_from=pct_from, pct_to=pct_to,
            ord_style=ord_style,
        )
        if raw_inputs:
            inputs.update({int(k): v for k, v in raw_inputs.items()})

        def configure(obj: object) -> None:
            for index in sorted(inputs):
                obj.SetInputValue(index, inputs[index])

        p.wait_for_request_slot(CybosPlus.REQ_TYPE_QUOTE)
        obj = p.block_once("rank", configure, ok_returns=_PAGED_OK_RETURNS)
        items: List[Dict[str, Any]] = []
        pages = 0
        while True:
            pages += 1
            count = _num(obj.GetHeaderValue(0))
            for row in range(count):
                code = _plain_stock_code(obj.GetDataValue(0, row))
                if code is None:
                    continue  # indices/futures/ETN rows carry non-stock codes
                items.append(
                    {
                        "code": code,
                        "name": obj.GetDataValue(1, row),
                        "diff_sign": obj.GetDataValue(3, row),
                        "diff": obj.GetDataValue(4, row),
                        "volume": obj.GetDataValue(6, row),
                        "rank": len(items) + 1,
                    }
                )
                if len(items) >= max(1, int(limit)):
                    break
            if len(items) >= max(1, int(limit)):
                break
            if not getattr(obj, "Continue", False):
                break
            p.wait_for_request_slot(CybosPlus.REQ_TYPE_QUOTE)
            p.block_again(obj, ok_returns=_PAGED_OK_RETURNS)

        return {"items": items, "total_count": _num(obj.GetHeaderValue(1)), "pages": pages}

    return session.invoke(job)  # type: ignore[return-value]
