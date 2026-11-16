"""미국장/환율 as-of 피처 — us_market_data 기반 (opt-in, 기본 OFF: env US_MARKET_FEATURES=1).

왜 이 축인가 (2026-11-16 실측):
  · `macro_*`(환율·금리·유가) 단변량 edge |AUC-0.5| = 0.0348 로 감성(0.0030)·이벤트(0.0014)의 10배.
  · 미국장은 한국장 **전 세션**에 끝나므로(t−1 미장 → t 한국장) 시점정합이 깨지지 않는 드문 축이다.
  · `us_market_data` 는 2026-11-16 이전까지 0행(컨테이너에 yfinance 없음)이었고, reader 도 없었다.
    이제 `scripts/collect_us_market.py` 가 크론(화~토 07:40)으로 채우고, 이 모듈이 소비한다.

설계 원칙:
  · **기본 OFF** — env `US_MARKET_FEATURES=1` 일 때만 피처 이름/값이 추가된다. 미설정이면 종전과
    비트 동일(패널 스키마 변경 없음).
  · **as-of** — 조회일 t 에 대해 `trade_date < t` 의 마지막 미국 세션만 쓴다(당일 미장은 아직 없다).
  · fail-open — 테이블/데이터가 없으면 전부 0.0 을 돌려주고 예외를 밖으로 내보내지 않는다.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# (피처명, us_market_data.index_name)
_INDEX_MAP = {
    "us_nasdaq_ret_1d": "NASDAQ",
    "us_sox_ret_1d": "SOX",
    "us_sp500_ret_1d": "SP500",
    "us_vix_level": "VIX",
    "us_vix_change_1d": "VIX",
    "usd_krw_change_1d": "USDKRW",
    "kospi200_night_ret_1d": "KOSPI200_NIGHT",
}


def feature_names() -> List[str]:
    return list(_INDEX_MAP.keys())


def _fetch(pg_conn, date: str) -> Dict[str, List[tuple]]:
    """조회일 이전(엄격히 < date) 최근 3세션의 (index_name → [(date, close) 내림차순])."""
    out: Dict[str, List[tuple]] = {}
    if pg_conn is None:
        return out
    try:
        cur = pg_conn.cursor()
        cur.execute(
            """
            SELECT index_name, trade_date, close_price
            FROM us_market_data
            WHERE trade_date < %s::date AND close_price IS NOT NULL
            ORDER BY index_name, trade_date DESC
            """,
            (date,),
        )
        for name, d, close in cur.fetchall():
            out.setdefault(name, []).append((d, float(close)))
        cur.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("us_market_data unavailable (%s); us features stay 0.0", e)
        return {}
    return {k: v[:3] for k, v in out.items()}


def _pct(a: Optional[float], b: Optional[float]) -> float:
    if a is None or b in (None, 0):
        return 0.0
    return (a - b) / b * 100.0


def compute(pg_conn, date: str) -> Dict[str, float]:
    """us_* 피처 7개. 데이터가 없으면 0.0(키는 항상 존재 → 벡터 길이 안정)."""
    feats = {n: 0.0 for n in _INDEX_MAP}
    series = _fetch(pg_conn, str(date)[:10])
    if not series:
        return feats

    for feat, idx in _INDEX_MAP.items():
        rows = series.get(idx) or []
        if not rows:
            continue
        if feat == "us_vix_level":
            feats[feat] = rows[0][1]
        elif feat.endswith("_change_1d") and len(rows) >= 2:
            feats[feat] = _pct(rows[0][1], rows[1][1])
        elif feat.endswith("_ret_1d") and len(rows) >= 2:
            feats[feat] = _pct(rows[0][1], rows[1][1])
        elif feat.endswith("_ret_1d"):
            # 세션 1개만 있으면 1일 수익률을 만들 수 없다 → 0.0 유지
            continue
    return feats


class UsMarketFeatures:
    """feature_pipeline 과 동일한 호출 규약(get_all_features)으로 감싼다."""

    def get_all_features(self, pg_conn, date: str) -> Dict[str, float]:
        return compute(pg_conn, date)
