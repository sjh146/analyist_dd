#!/usr/bin/env python3
"""KIS 엔드포인트 전수 프로브 — "이 계정으로 무엇을 수집할 수 있나"를 1콜씩 확인한다.

사전 준비:
    set -a; . <repo>/.env; set +a          # KIS_APP_KEY / KIS_APP_SECRET / KIS_TOKEN_PATH
실행:
    python3 scripts/kis_probe_all.py [--delay 1.2] [--collector <path-to-services/kis-collector>]

자격증명·토큰은 출력하지 않는다. 실패는 재시도 없이 msg_cd/msg1 을 그대로 보고한다
(KIS 레이트리밋을 태우지 않기 위해). 엔드포인트 추가는 PROBES 리스트에만 넣으면 된다.
"""
import argparse
import json
import logging
import os
import sys
import time

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

# (라벨, path, tr_id, params)
PROBES = [
    ("국내주식 현재가", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
    ("ETF 현재가", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "069500"}),
    ("국내 일봉", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930", "FID_INPUT_DATE_1": "20260901",
      "FID_INPUT_DATE_2": "20260923", "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}),
    ("ETF 일봉", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "069500", "FID_INPUT_DATE_1": "20260901",
      "FID_INPUT_DATE_2": "20260923", "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}),
    ("국내 분봉", "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930", "FID_INPUT_HOUR_1": "153000",
      "FID_PW_DATA_INCU_YN": "Y", "FID_ETC_CLS_CODE": ""}),
    ("국내 호가", "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn", "FHKST01010200",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
    ("국내 체결", "/uapi/domestic-stock/v1/quotations/inquire-ccnl", "FHKST01010300",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
    ("국내 지수", "/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
     {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"}),
    ("거래량 순위", "/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171", "FID_INPUT_ISCD": "0000",
      "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "111111111",
      "FID_TRGT_EXLS_CLS_CODE": "0000000000", "FID_INPUT_PRICE_1": "0", "FID_INPUT_PRICE_2": "0",
      "FID_VOL_CNT": "0"}),
    ("종목정보(마스터)", "/uapi/domestic-stock/v1/quotations/search-info", "CTPF1002R",
     {"PRDT_TYPE_CD": "300", "PDNO": "069500"}),
    ("종목별 투자자매매동향", "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily", "FHPTJ04160001",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930", "FID_INPUT_DATE_1": "20260901",
      "FID_ORG_ADJ_PRC": "0", "FID_ETC_CLS_CODE": "1"}),
    ("국내선물 현재가", "/uapi/domestic-futureoption/v1/quotations/inquire-price", "FHMIF10000000",
     {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": "101S09"}),
    ("해외주식 현재가", "/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
     {"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL"}),
    ("해외주식 현재가(원유 ETF)", "/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
     {"AUTH": "", "EXCD": "NYS", "SYMB": "USO"}),
    ("해외주식 일봉", "/uapi/overseas-price/v1/quotations/dailyprice", "HHDFS76240000",
     {"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL", "GUBN": "0", "BYMD": "20260923", "MODP": "1"}),
    ("시장별 투자자(시간)", "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market", "FHPTJ04040000",
     {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001", "FID_INPUT_ISCD_1": "0001",
      "FID_INPUT_DATE_1": "20260923", "FID_INPUT_HOUR_1": "150000"}),
    ("해외선물 현재가", "/uapi/overseas-futureoption/v1/quotations/price", "HHDFC55020100",
     {"SRS_CD": "CLZ26", "EXCD": "CME"}),
]


def summarize(data: dict) -> str:
    out = data.get("output")
    out1 = data.get("output1")
    if isinstance(out, dict):
        return f"output(dict,{len(out)}키)"
    if isinstance(out, list):
        return f"output(list,{len(out)}건)"
    return f"output1({len(out1)}키)" if isinstance(out1, dict) else "output 없음"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--collector", default=None,
                    help="services/kis-collector 경로 (기본: 이 스킬 기준 추정)")
    args = ap.parse_args()

    collector = args.collector or os.path.expanduser("~/analyist_dd/services/kis-collector")
    sys.path.insert(0, collector)
    try:
        from kis_app.client.kis_client import KisApiError, KisClient, KisTransportError
    except ImportError as e:
        print(f"kis-collector 클라이언트를 찾지 못했습니다: {e}\n--collector 로 경로를 지정하세요")
        return 2

    if not os.environ.get("KIS_APP_KEY") or not os.environ.get("KIS_APP_SECRET"):
        print("KIS_APP_KEY / KIS_APP_SECRET 미설정 — .env 를 source 하세요")
        return 2

    client = KisClient(
        os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"],
        os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
        delay=args.delay, jitter=0.3, retry_max=0, http_timeout=25,
        token_path=os.environ.get("KIS_TOKEN_PATH"),
    )

    ok, denied, failed = [], [], []
    for label, path, tr_id, params in PROBES:
        try:
            data = client._request(path, tr_id, params)
            ok.append(label)
            print(f"[OK]     {label:<26} {tr_id:<14} {summarize(data)}")
        except KisApiError as e:
            grep_msg = e.msg1 or ""
            if any(k in grep_msg for k in ("신청", "권한", "제공")):
                denied.append((label, e.msg_cd, grep_msg))
                print(f"[DENIED] {label:<26} {tr_id:<14} {e.msg_cd}: {grep_msg[:70]}")
            else:
                failed.append((label, e.msg_cd, grep_msg))
                print(f"[ERROR]  {label:<26} {tr_id:<14} {e.msg_cd}: {grep_msg[:70]}")
        except KisTransportError as e:
            failed.append((label, "TRANSPORT", str(e)))
            print(f"[FAIL]   {label:<26} {tr_id:<14} {str(e)[:70]}")
        time.sleep(0.0)

    print("\n요약: OK %d / DENIED %d / ERROR %d" % (len(ok), len(denied), len(failed)))
    print(json.dumps({"ok": ok, "denied": denied, "failed": failed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
