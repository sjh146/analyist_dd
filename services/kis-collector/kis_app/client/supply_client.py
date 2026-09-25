"""KIS 투자자 수급·지분율 전용 클라이언트 확장.

기존 :class:`KisClient`(일봉/분봉)는 그대로 두고 **서브클래스**로 얹는다 —
다른 작업자가 kis_client.py 를 동시에 수정해도 충돌하지 않게 하기 위함이다.
토큰 캐시·재시도·호출 간 지연은 부모 것을 그대로 재사용한다(``_quotes``).

실측 확정 (2026-09-24, 이 계정/앱키)
────────────────────────────────────────────────────────────────────────────
1) ``/uapi/domestic-stock/v1/quotations/inquire-investor`` (tr_id ``FHKST01010900``)
   · rt_cd=0, 응답 배열 키는 **``output``** 이다(``output2`` 가 아니다 — 문서/추측과 다름).
   · **1회 응답 최대 30행**(기간별 시세와 같은 30행 상한). 요청 구간이 60영업일이면
     2콜이 필요하다 — 30행 초과분을 기대하고 1콜만 돌리면 최근 30일만 적재된다.
   · 정렬은 날짜 **내림차순**.
   · 행 필드: stck_bsop_date(YYYYMMDD), stck_clpr, prsn_ntby_qty/frgn_ntby_qty/orgn_ntby_qty,
     prsn_ntby_tr_pbmn/frgn_ntby_tr_pbmn/orgn_ntby_tr_pbmn, *_shnu_vol, *_seln_vol, *_tr_pbmn.
   · ``*_tr_pbmn`` 단위는 **백만원** — 005930 20260923 실측으로 교차검증:
     frgn_ntby_qty 4,513,767주 × 종가 286,500원 = 1.293e12원 ≈ frgn_ntby_tr_pbmn 1,283,306 × 1e6.

2) ``/uapi/domestic-stock/v1/quotations/inquire-price`` (tr_id ``FHKST01010100``)
   · 지분율 관련 필드는 ``hts_frgn_ehrt``(외국인 지분율 %) · ``frgn_hldn_qty``(외국인 보유주식수)
     · ``lstn_stcn``(상장주식수). 005930 실측: 2,726,977,583 / 5,846,278,608 = 46.64% =
     ``hts_frgn_ehrt`` → 필드 의미 확정.
   · **기관 지분율 필드는 없다**(전체 output 필드 전수 확인). 과거 시점 조회도 불가(스냅샷만).
"""
from __future__ import annotations

import logging

from kis_app.client.kis_client import KisClient

logger = logging.getLogger("kis_collector.supply_client")

INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor"
INVESTOR_TR_ID = "FHKST01010900"
# 이력 조회용 — 실측(2026-09-24): inquire-investor 는 날짜 파라미터를 **완전히 무시**하고
# 항상 '최근 30거래일'만 준다(구간·PERIOD_DIV 를 어떻게 줘도 20260812~20260923 고정).
# investor-trade-by-stock-daily 는 FID_INPUT_DATE_1 을 **앵커**로 그 날짜부터 과거 30거래일을
# 돌려준다(DATE_1=20250601 → 20250416~20250530 실측) → DATE_1 을 뒤로 밀면 임의 깊이 확보.
DAILY_INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
DAILY_INVESTOR_TR_ID = "FHPTJ04160001"
PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
PRICE_TR_ID = "FHKST01010100"

# 1콜 응답 상한 (실측 30행) — 페이지네이션 안전 상한
MAX_ROWS_PER_CALL = 30


class SupplyClient(KisClient):
    """투자자별 매매동향 + 현재가(지분율) 조회."""

    def get_investor_flow(self, symbol, excd, date_from, date_to, org_adj_prc="0"):
        """투자자별 매매동향 1페이지 (최대 30행, 날짜 내림차순) → 전체 응답 dict."""
        params = {
            "FID_COND_MRKT_DIV_CODE": str(excd),
            "FID_INPUT_ISCD": str(symbol),
            "FID_INPUT_DATE_1": str(date_from),
            "FID_INPUT_DATE_2": str(date_to),
            "FID_ORG_ADJ_PRC": str(org_adj_prc),
            "FID_ETC_CLS_CODE": "0",
        }
        return self._quotes(INVESTOR_PATH, INVESTOR_TR_ID, params)

    def get_investor_flow_all(self, symbol, excd, date_from, date_to, max_pages=6):
        """구간 수급 이력을 과거로 파고들며 수집 (최신순 리스트).

        ``FHPTJ04160001 /investor-trade-by-stock-daily`` 를 쓴다. 이 TR 은
        ``FID_INPUT_DATE_1`` 을 앵커로 **그 날짜까지의 30거래일**을 ``output2`` 에 담아 준다
        (실측: DATE_1=20250601 → 20250416~20250530). 따라서 앵커를 '가장 오래된 날짜 −1일'로
        계속 밀면 임의 깊이의 이력을 얻는다 — ``FHKST01010900``(날짜 무시, 최근 30일 고정)로는
        60영업일 적재가 불가능하다.

        반환: 원시 KIS 행 dict 리스트(``stck_bsop_date`` 포함, 날짜 내림차순).
        """
        from datetime import timedelta

        from kis_app.utils import to_date

        collected = []
        seen = set()
        anchor = to_date(date_to) or to_date(date_from)
        floor = to_date(date_from)
        if anchor is None or floor is None:
            raise ValueError(f"잘못된 날짜 구간: {date_from}~{date_to}")
        for page in range(int(max_pages)):
            params = {
                "FID_COND_MRKT_DIV_CODE": str(excd),
                "FID_INPUT_ISCD": str(symbol),
                "FID_INPUT_DATE_1": anchor.strftime("%Y%m%d"),
                "FID_ORG_ADJ_PRC": "0",
                "FID_ETC_CLS_CODE": "0",
            }
            resp = self._quotes(DAILY_INVESTOR_PATH, DAILY_INVESTOR_TR_ID, params)
            rows = [r for r in (resp.get("output2") or [])
                    if isinstance(r, dict) and r.get("stck_bsop_date")]
            if not rows:
                break
            added = 0
            for r in rows:
                d = to_date(r.get("stck_bsop_date"))
                if d is None or d in seen:
                    continue
                seen.add(d)
                collected.append(r)
                added += 1
            oldest = min(to_date(r["stck_bsop_date"]) for r in rows)
            logger.info("%s 수급 %d페이지 anchor=%s %d행 (최고 %s, 추가 %d, 누적 %d)",
                        symbol, page + 1, anchor, len(rows), oldest, added, len(collected))
            if added == 0 or oldest <= floor:
                break
            anchor = oldest - timedelta(days=1)
        collected.sort(key=lambda r: str(r.get("stck_bsop_date")), reverse=True)
        return collected

    def get_price(self, symbol, excd="J"):
        """현재가 1콜 — 지분율(hts_frgn_ehrt) 스냅샷 조회용."""
        params = {
            "FID_COND_MRKT_DIV_CODE": str(excd),
            "FID_INPUT_ISCD": str(symbol),
        }
        return self._quotes(PRICE_PATH, PRICE_TR_ID, params)
