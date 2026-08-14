"""Universe selection for backtests and model retraining.

WHY (2026-08): the backtest universe was ``ORDER BY stock_code LIMIT 50`` — the
50 lowest stock codes, i.e. mostly early KOSPI listings plus bond/commodity
ETNs (TIGER 국고채 ETN, 콩 선물 ETN, ...). A biased, non-random sample makes
backtest results untrustworthy.

This module provides a **deterministic stratified random sample of common
stocks only** (ETF/ETN excluded by name pattern), so backtests and retraining
see a representative KOSPI+KOSDAQ universe. Deterministic seeds keep runs
reproducible.
"""

import logging
import random
from datetime import datetime, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)

# ETF/ETN/파생상품 이름 패턴 — 일반 주식명과 겹치지 않는 안전 세트.
# ('금'/'은' 단독 등은 금호석유 같은 실주를 걸러낼 수 있어 제외)
ETF_ETN_PATTERNS = (
    "%ETN%", "%ETF%", "%레버리지%", "%인버스%", "%리버스%",
    "%KODEX%", "%TIGER%", "%RISE%", "%HANARO%", "%ARIRANG%", "%KBSTAR%",
    "%커버드콜%", "%국고채%", "%채권%", "%파생%", "%선물%",
    "%골드%", "%원유%", "%천연가스%", "%금선물%", "%은선물%",
    "%리츠%", "%2X%", "%3X%", "% ETF%", "% ETN%",
)


def is_etf_etn(stock_name: Optional[str]) -> bool:
    """True if the stock name looks like an ETF/ETN/derivative product."""
    if not stock_name:
        return False
    upper = stock_name.upper()
    return any(p.strip("%").upper() in upper for p in ETF_ETN_PATTERNS)


def _fetch_eligible(pg, date_from: str, min_days: int) -> List[dict]:
    """주식(ETF/ETN 제외) + 최근 min_days일 이상 거래된 종목 목록."""
    cur = pg.cursor()
    cur.execute(
        """
        SELECT s.stock_code, s.stock_name, s.market, MAX(md.trade_date) AS latest
        FROM stocks s
        JOIN market_data md ON s.stock_code = md.stock_code AND md.trade_date >= %s
        WHERE s.market IN ('KOSPI', 'KOSDAQ')
        GROUP BY s.stock_code, s.stock_name, s.market
        HAVING COUNT(md.trade_date) >= %s
        """,
        (date_from, min_days),
    )
    rows = [{"code": r[0], "name": r[1], "market": r[2], "latest": r[3]} for r in cur.fetchall()]
    cur.close()
    return [r for r in rows if not is_etf_etn(r["name"])]


def _default_date_from(days: int = 60) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def select_backtest_universe(
    pg,
    n_kospi: int = 30,
    n_kosdaq: int = 20,
    min_days: int = 30,
    seed: int = 42,
    date_from: Optional[str] = None,
) -> List[str]:
    """KOSPI n_kospi + KOSDAQ n_kosdaq 무작위 층화 표본 (seed 고정 → 재현 가능).

    각 시장 풀에서 모자라면 있는 만큼만 사용한다.
    """
    date_from = date_from or _default_date_from()
    eligible = _fetch_eligible(pg, date_from, min_days)
    pools = {"KOSPI": [r["code"] for r in eligible if r["market"] == "KOSPI"],
             "KOSDAQ": [r["code"] for r in eligible if r["market"] == "KOSDAQ"]}
    rng = random.Random(seed)
    picked = []
    for market, n in (("KOSPI", n_kospi), ("KOSDAQ", n_kosdaq)):
        pool = pools.get(market, [])
        picked.extend(rng.sample(pool, min(n, len(pool))))
        logger.info("backtest universe %s: %d/%d", market, min(n, len(pool)), len(pool))
    rng.shuffle(picked)
    return picked


def select_training_universe(
    pg,
    limit: int = 200,
    min_days: int = 30,
    seed: int = 0,
    date_from: Optional[str] = None,
) -> List[str]:
    """재학습용 유니버스 — ETF/ETN 제외 + 최근 데이터 순 (limit) + seed 셔플.

    최신 데이터를 우선하되 동률 구간은 seed 고정 랜덤으로 편향을 줄인다.
    """
    date_from = date_from or _default_date_from()
    eligible = _fetch_eligible(pg, date_from, min_days)
    eligible.sort(key=lambda r: (r["latest"] is None, r["latest"]), reverse=True)
    top = eligible[: max(limit * 3, 30)]
    rng = random.Random(seed)
    rng.shuffle(top)
    picked = [r["code"] for r in top[:limit]]
    logger.info("training universe: %d stocks (limit %d)", len(picked), limit)
    return picked
