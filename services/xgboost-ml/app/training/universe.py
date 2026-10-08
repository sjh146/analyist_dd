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
import os
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
          AND s.instrument_type = 'STOCK'
        GROUP BY s.stock_code, s.stock_name, s.market
        HAVING COUNT(md.trade_date) >= %s
        ORDER BY s.stock_code
        """,
        (date_from, min_days),
    )
    rows = [{"code": r[0], "name": r[1], "market": r[2], "latest": r[3]} for r in cur.fetchall()]
    cur.close()
    return [r for r in rows if not is_etf_etn(r["name"])]


def _default_date_from(days: int = 60) -> str:
    """적격 창 시작일 = (UNIVERSE_ASOF_DATE | 오늘) − days.

    유니버스 동결(측정 정합성, 2026-10-09 실측): 종전 구현은 `datetime.now() − days` 라
    **날짜가 바뀌면 창이 미끄러지고**, 적격 집합이 아주 조금만 달라져도(실측: 2,652 vs 2,657행
    = 5종목 차이) `rng.shuffle(eligible)` 의 **입력 순서**가 바뀌어 `top[:limit]` 이 통째로
    재추첨된다 → 같은 seed=0 인데도 선택 200종목의 교집합이 26/200(=87% 교체)이었다.
    그 결과 (a) 프로덕션 재학습 유니버스가 매일 다른 표본이 되고(패널은 end-date 고정이라
    평가 표본과 최대 31/200 만 일치), (b) '같은 유니버스' 를 전제한 실험 비교가 표본 교체와
    뒤섞인다. `UNIVERSE_ASOF_DATE=YYYY-MM-DD` 를 지정하면 창이 고정돼 유니버스가 재현된다.
    미설정이면 종전과 **비트 동일**(현행 유지) — 동결 여부는 운영 결정이다.
    """
    asof = os.environ.get("UNIVERSE_ASOF_DATE")
    ref = datetime.strptime(asof, "%Y-%m-%d") if asof else datetime.now()
    return (ref - timedelta(days=days)).strftime("%Y-%m-%d")


def _fetch_liquid(pg, date_from: str, min_days: int, window_days: int = 60) -> List[dict]:
    """일평균 거래대금(close×volume) 상위 종목 — **결정적** 정렬(대금 DESC, 코드 ASC).

    왜 거래대금인가: 스크리너·트레이더가 실제로 다루는 후보는 거래대금이 큰 종목이다. 학습 표본을
    서빙 대상 분포에 정렬하면 ① 커버리지(SNS·뉴스) 교집합이 올라가고 ② 표본당 정보량이 커진다.

    컬럼 실측(2026-10-01): market_data 의 거래대금 컬럼은 `trading_value` 인데 최근 60일 153,604행 중
    107,522행(70%)만 채워져 있다 → `COALESCE(trading_value, close_price*volume)` 로 100% 커버한다.
    (컬럼명은 `close_price` 다 — `close` 아님. 오타로 실행하면 UndefinedColumn 으로 즉시 죽는다.)

    정렬에 임의성을 남기지 않는 이유(2026-10-01 실측): 종전 recency 경로는 동률 그룹 안에서
    SQL 반환 순서가 유니버스를 정해 같은 모델 AUC 가 0.017 흔들렸다. 여기서는 두 번째 키까지
    고정해 4회 호출 교집합이 100% 임을 테스트로 증명한다(scripts/_universe_mode_test.py).
    """
    cur = pg.cursor()
    cur.execute(
        """
        SELECT s.stock_code, s.stock_name, s.market,
               COUNT(md.trade_date) AS n_days,
               AVG(COALESCE(md.trading_value,
                            COALESCE(md.close_price, 0) * COALESCE(md.volume, 0))) AS avg_value
        FROM stocks s
        JOIN market_data md ON s.stock_code = md.stock_code AND md.trade_date >= %s
        WHERE s.market IN ('KOSPI', 'KOSDAQ')
          AND s.instrument_type = 'STOCK'
        GROUP BY s.stock_code, s.stock_name, s.market
        HAVING COUNT(md.trade_date) >= %s
        ORDER BY avg_value DESC, s.stock_code ASC
        """,
        (date_from, min_days),
    )
    rows = [{"code": r[0], "name": r[1], "market": r[2], "n_days": r[3],
             "avg_value": float(r[4] or 0.0)} for r in cur.fetchall()]
    cur.close()
    # ETF/ETN/파생 제외는 종목명 패턴으로만(거래대금이 큰 ETF 가 상위를 차지하는 것을 막는다).
    return [r for r in rows if not is_etf_etn(r["name"]) and r["avg_value"] > 0]


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
    mode: str = "recency",
) -> List[str]:
    """재학습용 유니버스 — ETF/ETN 제외 + (mode) 선택.

    mode="recency"(기본, 현행 동작 비트 동일): 최신 데이터 순 (limit) + seed 셔플.
    mode="liquidity"(2026-10-01 CG57): 최근 구간 일평균 거래대금(close×volume) 상위 limit.
        왜: 현행 recency 는 실측상 **무작위 표본**이다(2,543/2,655종목이 같은 latest 라 동률
        그룹이 top 컷보다 크다) → 학습 표본이 실제 서빙 대상(스크리너 후보 = 거래대금 큰 종목)과
        어긋나고, 신규 원천(SNS·뉴스)과의 교집합도 9.5% 에 머문다. 유동성 정렬은 **결정적**
        (ORDER BY avg_value DESC, code ASC)이므로 짝 비교의 표본이 재현된다.
    """
    date_from = date_from or _default_date_from()
    if mode == "liquidity":
        picked = [r["code"] for r in _fetch_liquid(pg, date_from, min_days)][:limit]
        logger.info("training universe(liquidity): %d stocks (limit %d)", len(picked), limit)
        return picked
    if mode != "recency":
        raise ValueError(f"unknown universe mode: {mode!r} (recency|liquidity)")
    eligible = _fetch_eligible(pg, date_from, min_days)
    # 결정성(2026-10-01 실측 수리): 종전에는 ORDER BY 없이 `sort(key=latest)` 를 썼는데
    # 실측상 2,655종목 중 **2,543종목이 같은 latest(최근 거래일)** 라 동률 그룹이 top 컷
    # (limit*3=180/600)보다 커서, `eligible[:limit*3]` 이 동률 내부를 잘랐다 → SQL 반환 순서
    # (실행마다 달라짐)가 유니버스를 정했다. 실측: 같은 커넥션 4회 실행에서 limit=60 교집합
    # [60, 52, 57, 54] · limit=200 [200, 184, 181, 171]. 그래서 같은 모델·같은 창·같은 프로토콜의
    # 견고 AUC 가 0.5163 → 0.5336 으로 재측정마다 0.017 흔들렸다(+0.02 문턱이 잡음 안에 있었다).
    # 수리: ① 쿼리에 ORDER BY ② 정렬을 2단(코드 오름차순 → 최신일 내림차순)으로 분리해
    # latest 동률은 항상 코드로 깨진다. 의미(최신 데이터 우선 + seed 셔플)는 그대로다.
    eligible.sort(key=lambda r: r["code"])
    rng = random.Random(seed)
    # 동률(latest) 구간을 **seed 고정 랜덤**으로 깨라 — docstring 의 의도가 이것이다
    # ("동률 구간은 seed 고정 랜덤으로 편향을 줄인다"). 코드 알파벳순으로 깨면 '코드 앞 N개'라는
    # 과거 실측 편향(UNIVERSE_SQL ORDER BY stock_code LIMIT 50 → 알파벳 앞 50종목만 학습)을
    # limit*3 컷 규모로 재도입하게 된다 — 무편향 tie-break 는 랜덤이되 결정적이어야 한다.
    rng.shuffle(eligible)
    eligible.sort(key=lambda r: (r["latest"] is None, str(r["latest"])), reverse=True)
    top = eligible[: max(limit * 3, 30)]
    rng.shuffle(top)
    picked = [r["code"] for r in top[:limit]]
    logger.info("training universe: %d stocks (limit %d)", len(picked), limit)
    return picked
