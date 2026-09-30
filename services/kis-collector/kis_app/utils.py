"""공용 파싱/시간 헬퍼 (KIS 문자열 → 숫자/날짜, HHMMSS 산술)."""
from __future__ import annotations

import logging
from datetime import date, datetime

logger = logging.getLogger("kis_collector.utils")

try:  # tzdata 없는 이미지 대비
    from zoneinfo import ZoneInfo

    _KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover
    from datetime import timedelta, timezone

    _KST = timezone(timedelta(hours=9))

# 장 마감 + 여유 (KST). 이 시각 전의 '당일 봉'은 미완성 스냅샷이다.
MARKET_CLOSE_BUFFER_HOUR = 15
MARKET_CLOSE_BUFFER_MINUTE = 40


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


def kst_now() -> datetime:
    """컨테이너 TZ(보통 UTC)와 무관한 한국 시각."""
    return datetime.now(_KST)


def kst_market_closed(now: datetime | None = None) -> bool:
    """한국 장 마감(+여유) 이후인가."""
    now = now or kst_now()
    return (now.hour, now.minute) >= (MARKET_CLOSE_BUFFER_HOUR, MARKET_CLOSE_BUFFER_MINUTE)


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return to_date(value)


def is_unfinished_daily_bar(trade_date, now: datetime | None = None) -> bool:
    """미완성 일봉이라 적재하면 안 되는가 (당일 장중·장 개시 전, 미래 날짜).

    실측 2026-10-01 04:19: data_gap 백필이 **당일(10/01)을 공실로 판정**해 KIS 일봉을
    장 개시 전에 돌렸고, KIS 가 '전일 종가 = 시/고/저/종가, 거래량 0' 인 미완성 당일 봉을
    돌려주어 525종목(실행 지속 중)이 평탄·거래량 0 봉으로 market_data 에 적재됐다.
    파급: market_data_freshness_days = -1(신선도 왜곡), 수급 지연 프로브 판정이
    종전 2(통과) → 3(미달) 로 뒤집힘. yfinance-collector 는 c5d2c08 에서 같은 가드를
    갖췄지만 KIS 경로에는 없었다 — 공식 KRX/KIS 경로가 18:55·20:00 에 확정 봉을 넣으므로
    그 전의 당일 봉은 버려야 한다.
    """
    d = _as_date(trade_date)
    if d is None:
        return False  # 파싱 불가 행은 종전대로 호출자가 판단
    now = now or kst_now()
    today = now.date()
    if d > today:
        return True
    if d == today:
        return not kst_market_closed(now)
    return False
