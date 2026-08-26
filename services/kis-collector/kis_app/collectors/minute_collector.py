"""분봉 수집기 — KIS inquire-time-itemchartprice → 신규 ``minute_bars``.

수집 범위 설계: **장 마감 후 전 종목 당일 1회** (30분 창 채점은 다음날 개장 후
필요하므로 당일 분봉은 마감 후 수집으로 충분. 실시간 수집은 쌍둥이 실시간
분봉에서 별도 처리).

페이지네이션: 1회 응답 최대 100건(FID_CNT). 응답은 시간 내림차순(최신 우선)
이므로, ``FID_INPUT_HOUR=153000`` 시작 → 페이지의 최저 시각 −1분을 다음 요청
기준으로 과거로 진행. ``090000`` 도달 / 배치 < FID_CNT / max_pages 방어로 종료.
"""
from __future__ import annotations

import logging

from kis_app.utils import add_minutes, to_date, to_float, to_int
from kis_app.collectors.daily_collector import market_to_excd

logger = logging.getLogger("kis_collector.minute")

MARKET_OPEN_TIME = "090000"
MARKET_CLOSE_TIME = "153000"
FID_PERIOD_1MIN = "0"       # FID_PERIOD_DIV: 0 = 1분봉
FID_CNT_DEFAULT = 100       # 1회 응답 최대 건수
MAX_PAGES_DEFAULT = 10      # 안전장치 (정규장 391분 → 100건×4페이지면 충분)


def parse_minute_bars(resp, target_date=None):
    """KIS 분봉 응답 output2 → minute_bars 행 리스트.

    필터: 대상일 + 장중(09:00:00~15:30:00) + 필수필드(stck_cntg_hour/stck_prpr).
    ``stck_prpr`` = 해당 분 종가.
    """
    out = []
    for raw in (resp or {}).get("output2") or []:
        if not isinstance(raw, dict):
            continue
        bsop = raw.get("stck_bsop_date")
        bar_time = raw.get("stck_cntg_hour")
        close = to_float(raw.get("stck_prpr"))
        if not bsop or not bar_time or close is None:
            continue
        if target_date and str(bsop) != str(target_date):
            continue
        if not (MARKET_OPEN_TIME <= str(bar_time) <= MARKET_CLOSE_TIME):
            continue
        out.append({
            "trade_date": to_date(bsop),
            "time": str(bar_time),
            "open_price": to_float(raw.get("stck_oprc")),
            "high_price": to_float(raw.get("stck_hgpr")),
            "low_price": to_float(raw.get("stck_lwpr")),
            "close_price": close,
            "volume": to_int(raw.get("cntg_vol")),
            "trading_value": to_float(raw.get("acml_tr_pbmn")),
        })
    return out


class MinuteCollector:
    """전 종목 당일 분봉 수집 → minute_bars upsert."""

    def __init__(self, client, storage):
        self._client = client
        self._storage = storage

    def collect_stock(self, stock_code, market, target_date, *,
                      fid_cnt=FID_CNT_DEFAULT, max_pages=MAX_PAGES_DEFAULT):
        """단일 종목 당일 분봉 전체 수집 (페이지네이션). 오름차순 정렬 반환."""
        excd = market_to_excd(market)
        bars = []
        seen = set()
        reference = MARKET_CLOSE_TIME
        for page in range(1, int(max_pages) + 1):
            resp = self._client.get_minute_chart(
                stock_code, excd, reference, period_div=FID_PERIOD_1MIN,
                fid_cnt=int(fid_cnt))
            page_bars = parse_minute_bars(resp, target_date=target_date)
            new = []
            for b in page_bars:
                if b["time"] not in seen:
                    seen.add(b["time"])
                    new.append(b)
            bars.extend(new)
            logger.debug("  %s page %d: %d건 (신규 %d, 기준 %s)",
                         stock_code, page, len(page_bars), len(new), reference)

            if not page_bars:
                break  # 데이터 없음
            oldest = min(b["time"] for b in page_bars)
            if len(page_bars) < int(fid_cnt) or oldest == MARKET_OPEN_TIME:
                break  # 마지막 페이지
            nxt = add_minutes(oldest, -1)
            if nxt >= reference:
                logger.warning("%s: 페이지 진행 없음(기준 %s→%s) — 중단",
                               stock_code, reference, nxt)
                break  # 무한루프 방어
            reference = nxt

        bars.sort(key=lambda b: (b["trade_date"] is None, b["trade_date"] or "",
                                 b["time"]))
        return bars

    def collect(self, target_date, limit=None):
        """대상일(YYYYMMDD) 전 종목 분봉 수집. limit=N이면 첫 N 종목만.

        반환: {"ok": 저장 성공 종목수, "no_data": 봉 없음, "fail": 오류,
               "total": 처리 종목수, "bars": 저장 봉 수}
        """
        universe = self._storage.get_universe()
        if limit is not None:
            universe = universe[: int(limit)]

        summary = {"ok": 0, "no_data": 0, "fail": 0, "total": len(universe),
                   "bars": 0}
        for idx, (code, market) in enumerate(universe, start=1):
            try:
                rows = self.collect_stock(code, market, target_date)
                if rows:
                    saved = self._storage.save_minute_bars(code, rows)
                    summary["ok"] += 1
                    summary["bars"] += saved
                    logger.info("[%d/%d] %s 분봉 %d행 저장",
                                idx, len(universe), code, saved)
                else:
                    summary["no_data"] += 1
                    logger.info("[%d/%d] %s — %s 분봉 없음",
                                idx, len(universe), code, target_date)
            except Exception as e:
                summary["fail"] += 1
                logger.warning("[%d/%d] %s 분봉 수집 실패: %s",
                               idx, len(universe), code, e)
        logger.info("분봉 수집 완료: %s", summary)
        return summary
