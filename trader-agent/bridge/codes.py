"""Stock code normalization helpers.

Public API codes are 6 digits, e.g. ``"005930"``.  CybosPlus trade inputs
(``CpTrade.*``) expect the prefixed form ``"A005930"`` exactly as the Daishin
samples do; balance/quote/position responses carry the prefix back and are
stripped to 6 digits before being returned.

Only the ``A`` prefix used by stock trade objects is normalised.  Other
Cybos instrument prefixes (e.g. ``Q`` futures/indices) are treated as opaque
and passed through untouched so this module never corrupts a foreign code.
"""

from __future__ import annotations

import re

_PLAIN_RE = re.compile(r"^[0-9]{6}$")
_TRADE_RE = re.compile(r"^A[0-9]{6}$")
_OTHER_PREFIX_RE = re.compile(r"^[A-Z][0-9]{6}$")


class InvalidCodeError(ValueError):
    """Raised when a string is not a recognisable CybosPlus stock code."""


def is_plain_code(code: str) -> bool:
    """Return True when *code* looks like a bare 6 digit stock code."""
    return _PLAIN_RE.match(code) is not None


def is_trade_code(code: str) -> bool:
    """Return True when *code* already carries the ``A`` trade prefix."""
    return _TRADE_RE.match(code) is not None


def to_trade_code(code: str) -> str:
    """Normalise *code* to the CybosPlus trade form ``A`` + 6 digits.

    Accepts either a plain 6-digit code or an already-prefixed code.
    Foreign prefixed codes (anything else that looks like a prefixed
    7-symbol code) are returned unchanged.

    :raises InvalidCodeError: if *code* is not a valid stock code.
    """
    s = code.strip().upper()
    if _TRADE_RE.match(s):
        return s
    if _PLAIN_RE.match(s):
        return "A" + s
    if _OTHER_PREFIX_RE.match(s):
        return s
    raise InvalidCodeError("not a valid 6 digit stock code: {0!r}".format(code))


def to_plain_code(code: str) -> str:
    """Strip the ``A`` prefix from *code* returning a bare 6 digit code.

    Bare 6-digit input is returned unchanged.  Foreign prefixed codes are
    returned unchanged (no prefix is stripped from them).

    :raises InvalidCodeError: if *code* is not a valid stock code.
    """
    s = code.strip().upper()
    if _PLAIN_RE.match(s):
        return s
    if _TRADE_RE.match(s):
        return s[1:]
    if _OTHER_PREFIX_RE.match(s):
        return s
    raise InvalidCodeError("not a valid stock code: {0!r}".format(code))
