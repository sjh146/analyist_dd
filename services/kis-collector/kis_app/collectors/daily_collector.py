"""일봉 수집기 — KIS inquire-daily-itemchartprice → 기존 ``market_data``.

유니버스: market_data에 존재하는 종목 (KRX 수집분 기준). market → EXCD 매핑
(KOSPI→KSS, KOSDAQ→KSQ). 개별 종목 실패는 로그+카운트 후 계속 진행.
"""
from __future__ import annotations

import logging

from kis_app.utils import is_unfinished_daily_bar, to_date, to_float, to_int

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

    def collect(self, target_date, limit=None, universe=None):
        """대상일(YYYYMMDD) 전 종목 수집. limit=N이면 첫 N 종목만 (점검용).

        universe(=[(code, market)]) 를 주면 DB 유니버스 대신 그것을 쓴다
        (분봉처럼 대상 종목을 좁힐 때 — 우선순위 유니버스 파일).

        반환: {"ok": 저장 성공 종목수, "no_data": 해당일 봉 없음,
               "fail": 오류 종목수, "total": 처리 종목수}
        """
        universe = list(universe) if universe is not None else self._storage.get_universe()
        if limit is not None:
            universe = universe[: int(limit)]

        summary = {"ok": 0, "no_data": 0, "fail": 0, "total": len(universe),
                   "unfinished": 0,
                   # 자기신고(R23)용 집계: recv=응답 원시 행, bars=실제 upsert 한 행.
                   # 두 값을 모두 세야 '응답은 왔는데 파서가 0행' (2026-09-24 유형)을 잡는다.
                   "recv": 0, "bars": 0}
        for idx, (code, market) in enumerate(universe, start=1):
            excd = market_to_excd(market)
            try:
                resp = self._client.get_daily_chart(
                    code, excd, target_date, target_date, count=DEFAULT_FID_CNT)
                raw = resp.get("output2") if isinstance(resp, dict) else None
                if raw is None and isinstance(resp, dict):
                    raw = resp.get("output")
                # source(recv) = **대상일** 원시 행만 센다.
                # WHY(실측 2026-10-09 07:50): data_gap 휴장 프로브가 `--job daily --date <오늘>
                # --limit 1` 로 장 개시 전 당일을 조회하면 KIS 는 output2 에 **전일 봉 1건**을
                # 돌려준다(당일 봉은 아직 없음). 종전엔 len(raw)=1 을 source 로 세어 claimed=0 과
                # 만나 `source>0 AND claimed=0` → `dq_claim_parse_failure=1` 이 매 영업일 07:50
                # 발생했다(정상 no_data 실행이 파서 버그로 오보). 파서(parse_daily_bars)의 계약이
                # '대상일 행'이므로 수신량도 대상일로 한정한다 — 그래야 '대상일 행이 있는데 0행 파싱'
                # (=진짜 키/필드 불일치)만 잡히고, '다른 날짜 행뿐'은 no_data 로 남는다.
                # (필드명 자체가 바뀌는 유형은 신선도 메트릭이 잡는다 — 자기신고의 역할이 아니다.)
                if isinstance(raw, list):
                    summary["recv"] += sum(
                        1 for r in raw
                        if isinstance(r, dict)
                        and str(r.get("stck_bsop_date") or "") == str(target_date))
                rows = parse_daily_bars(resp, target_date=target_date)
                # 미완성 봉(장 개시 전·장중 당일 봉, 미래 날짜)은 적재하지 않는다.
                # KIS 는 장 개시 전 당일 조회에 '전일 종가 = 시/고/저/종가, 거래량 0' 인
                # 스냅샷을 돌려준다 — 그대로 넣으면 신선도·지연 판정이 오염된다(utils 참조).
                if rows:
                    finished = [r for r in rows if not is_unfinished_daily_bar(r["trade_date"])]
                    summary["unfinished"] += len(rows) - len(finished)
                    rows = finished
                if rows:
                    saved = self._storage.save_market_data(code, rows)
                    summary["ok"] += 1
                    summary["bars"] += saved
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
