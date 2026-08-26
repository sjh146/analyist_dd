"""공용 파싱/시간 헬퍼 (KIS 문자열 → 숫자/날짜, HHMMSS 산술)."""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("kis_collector.utils")


def to_float(value):
    """KIS 문자열 숫자를 float로. 비어있거나 None이면 None.

    KIS 응답은 '0', '-290', '28760', '123.45' 같은 문자열로 온다.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        logger.debug("float 파싱 실패: %r", value)
        return None


def to_int(value):
    """KIS 문자열 정수(거래량 등)를 int로. 비어있으면 None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        logger.debug("int 파싱 실패: %r", value)
        return None


def to_date(yyyymmdd):
    """'YYYYMMDD' → datetime.date. 실패 시 None."""
    if not yyyymmdd:
        return None
    s = str(yyyymmdd).strip()
    if len(s) != 8 or not s.isdigit():
        logger.debug("날짜 파싱 실패: %r", yyyymmdd)
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        logger.debug("날짜 파싱 실패: %r", yyyymmdd)
        return None


def add_minutes(hhmmss: str, delta_min: int) -> str:
    """HHMMSS 문자열에 delta_min(음수 가능)을 더한 HHMMSS 반환 (24h 순환)."""
    h = int(hhmmss[0:2])
    m = int(hhmmss[2:4])
    s = int(hhmmss[4:6])
    total = (h * 60 + m + delta_min) % (24 * 60)
    return f"{total // 60:02d}{total % 60:02d}{s:02d}"
