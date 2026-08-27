"""일봉 수집기 — KIS inquire-daily-itemchartprice → 기존 ``market_data``.

유니버스: market_data에 존재하는 종목 (KRX 수집분 기준). market → EXCD 매핑
(KOSPI→KSS, KOSDAQ→KSQ). 개별 종목 실패는 로그+카운트 후 계속 진행.
"""
from __future__ import annotations

import logging

from kis_app.utils import to_date, to_float, to_int

logger = logging.getLogger("kis_collector.daily")

DEFAULT_FID_CNT = 5  # 대상일 1건 + 인접일 여유 (API 순서 비의존 파싱용)


def parse_daily_bars(resp, target_date=None):
    """KIS 일봉 응답 output2 → market_data 행 리스트 (오름차순 정렬).

    - ``stck_bsop_date`` / ``stck_clpr`` 누락 행은 스킵 (데이터 불완전).
    - ``target_date``(YYYYMMDD 문자열) 지정 시 해당일만 반환.
    - 알 수 없는 키는 무시 — 필드 스키마 불일치에도 파서는 안전.
    """
    out = []
    for raw in (resp or {}).get("output2") or []:
        if not isinstance(raw, dict):
            continue
        bsop = raw.get("stck_bsop_date")
        close = to_float(raw.get("stck_clpr"))
        if not bsop or close is None:
            continue
        if target_date and str(bsop) != str(target_date):
            continue
        out.append({
            "trade_date": to_date(bsop),
            "open_price": to_float(raw.get("stck_oprc")),
            "high_price": to_float(raw.get("stck_hgpr")),
            "low_price": to_float(raw.get("stck_lwpr")),
            "close_price": close,
            "volume": to_int(raw.get("cntg_vol") or raw.get("acml_vol")),
            "trading_value": to_float(raw.get("acml_tr_pbmn")),
        })
    # API 순서(내림/오름) 비의존 — 날짜 오름차순으로 정규화
    out.sort(key=lambda r: (r["trade_date"] is None, r["trade_date"] or ""))
    return out


def market_to_excd(market: str) -> str:
    """stocks.market 값 → KIS FID_COND_MRKT_DIV_CODE.

    2026-08-27 실측: 이 AppKey/TR(FHKST03010100/30200)에서 "K"(코스닥)는
    OPSQ2001 INVALID로 거부됨. "J"는 코스피·코스닥 모두 정상 반환
    (API가 종목코드로 시장 자동 인식 — 위더스제약 330350+J 검증 완료).
    → 모든 시장 "J" 고정.
    """
    return "J"


class DailyCollector:
    """전 종목 일봉 수집 → market_data upsert."""

    def __init__(self, client, storage):
        self._client = client
        self._storage = storage

    def collect(self, target_date, limit=None):
        """대상일(YYYYMMDD) 전 종목 수집. limit=N이면 첫 N 종목만 (점검용).

        반환: {"ok": 저장 성공 종목수, "no_data": 해당일 봉 없음,
               "fail": 오류 종목수, "total": 처리 종목수}
        """
        universe = self._storage.get_universe()
        if limit is not None:
            universe = universe[: int(limit)]

        summary = {"ok": 0, "no_data": 0, "fail": 0, "total": len(universe)}
        for idx, (code, market) in enumerate(universe, start=1):
            excd = market_to_excd(market)
            try:
                resp = self._client.get_daily_chart(
                    code, excd, target_date, target_date, count=DEFAULT_FID_CNT)
                rows = parse_daily_bars(resp, target_date=target_date)
                if rows:
                    saved = self._storage.save_market_data(code, rows)
                    summary["ok"] += 1
                    logger.info("[%d/%d] %s(%s) 일봉 %d행 저장",
                                idx, len(universe), code, excd, saved)
                else:
                    summary["no_data"] += 1
                    logger.info("[%d/%d] %s(%s) — %s 봉 없음(휴장?)",
                                idx, len(universe), code, excd, target_date)
            except Exception as e:
                summary["fail"] += 1
                logger.warning("[%d/%d] %s(%s) 일봉 수집 실패: %s",
                               idx, len(universe), code, excd, e)
                # 한도/빈도 제한 오류(EGW00123 일일·EGW00124 분당 등) → 즉시 중단
                # (계속 호출하면 차단 위험 — KRX 7일 차단 교훈)
                from kis_app.client.kis_client import KisApiError, RATE_LIMIT_CODES
                if isinstance(e, KisApiError) and e.msg_cd in RATE_LIMIT_CODES:
                    logger.error("KIS 호출 한도 도달(%s) — 수집 중단 (다음 크론에서 이어서)",
                                 e.msg_cd)
                    summary["quota_hit"] = True
                    break
        summary.setdefault("quota_hit", False)
        logger.info("일봉 수집 완료: %s", summary)
        return summary
