#!/usr/bin/env python3
"""KIS OpenAPI '국내주식 공매도 일별추이'(FHPST04830000) → krx_short_selling 적재.

왜 이 경로인가
--------------
`krx_short_selling` 의 원래 수집기(services/krx-collector/app/collectors/
short_selling_collector.py)는 **pykrx** 를 쓰고, pykrx 는 KRX 레거시 스크래핑
엔드포인트(`comm/bldAttendant/getJsonData.cmd`)를 호출한다. 그 경로는 과거 요청
제한 위반으로 IP 차단을 받은 이력이 있어 정책상 호출 금지다 → 테이블이 0행이었다.

KIS OpenAPI 의 공매도 일별추이 TR 은 실측으로 정상 동작한다(HTTP 200, rt_cd=0).
1콜로 요청 구간 전체가 오고(행 수 상한 없음 — 실측 66거래일 구간 = 66행),
응답은 **거래일 내림차순**이다.

필드 매핑 (KIS output2 → krx_short_selling)
-------------------------------------------
    stck_bsop_date  → trade_date      영업일자
    ssts_cntg_qty   → short_volume    그날 공매도 체결 수량
    ssts_tr_pbmn    → short_value     그날 공매도 거래대금
    ssts_vol_rlim   → short_ratio     그날 공매도 거래량 비중(%)
    acml_vol        → total_volume    그날 전체 거래량  ← feature_pipeline #19 가 읽는다

실측 대조(005930, 2026-08-31):
    KIS   acml_vol=18,270,969 / acml_tr_pbmn=4,647,038,997,556
    DB    market_data.volume=18,270,969 / trading_value=4,647,038,997,556  → 일치
    비중 검산: ssts_cntg_qty/acml_vol = 1,563,557/18,270,969 = 8.557% ≈ ssts_vol_rlim 8.56

채울 수 없는 것 (정직한 한계)
-----------------------------
이 TR 은 '공매도 **체결**(거래)'이지 '공매도 **잔고**(short interest)'가 아니다.
- 잔고 계열 후보 경로 6개 프로브(daily-short-sale-balance, inquire-short-sale-balance,
  short-sale-balance-trend, short-sale, ranking/short-sale-balance, daily-loan-balance)
  → 전부 HTTP 404. KIS 에 공매도 잔고 엔드포인트가 없다.
- KRX OpenAPI 서비스 목록에도 공매도 서비스가 없다(주식 카테고리 = 일별매매정보·
  종목기본정보 계열뿐).
→ `krx_short_selling.balance_quantity` 는 NULL 로 남는다.
→ 리더의 `short_interest_ratio` · `days_to_cover` 는 존재하지 않는 `short_interest`
   테이블을 읽으므로 이 러너로는 살릴 수 없다(공매도 '잔고' 데이터 자체가 없음).

사용법
------
    # 기본: market_data 최신 거래일 기준 최근 120일 창, 유동성 상위 30종목
    set -a && . ./.env && set +a
    /usr/bin/python3 scripts/kis_short_selling_backfill.py --limit 30

    # 대상만 확인(호출/쓰기 없음)
    /usr/bin/python3 scripts/kis_short_selling_backfill.py --limit 30 --dry-run

    # 전 종목으로 확장 (약 600종목 ≈ 35분, KIS 호출간 3.0s+지터)
    /usr/bin/python3 scripts/kis_short_selling_backfill.py --limit 0

호출 규율
---------
호출 간 3.0초 + 지터 0~0.5초(env `KIS_REQUEST_DELAY`/`KIS_REQUEST_JITTER`),
실행당 상한(`--max-calls`, 기본 600), 날짜별 진행파일로 재개,
자격증명 오류(EGW00102/EGW00103)는 재시도 없이 즉시 종료.

DB 접속: 호스트에서 127.0.0.1:5434 (env `PGHOST_HOST`/`PGPORT_HOST` 로 조정).
`POSTGRES_HOST`(=.env 의 'postgres')는 컨테이너 내부 이름이라 호스트에서 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import date, timedelta

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "services", "kis-collector"))

from kis_app.client.kis_client import (  # noqa: E402
    KisApiError, KisClient, KisTransportError,
)

SHORT_SALE_PATH = "/uapi/domestic-stock/v1/quotations/daily-short-sale"
SHORT_SALE_TR_ID = "FHPST04830000"

PROGRESS_PATH = os.environ.get(
    "KIS_SHORT_PROGRESS_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                 "data", "kis", "short_selling_progress.json"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kis_short_selling")

UPSERT = """
INSERT INTO krx_short_selling
  (trade_date, stock_code, stock_name, short_volume, short_value,
   total_volume, short_ratio)
VALUES %s
ON CONFLICT (trade_date, stock_code) DO UPDATE SET
  stock_name   = COALESCE(EXCLUDED.stock_name, krx_short_selling.stock_name),
  short_volume = EXCLUDED.short_volume,
  short_value  = EXCLUDED.short_value,
  total_volume = EXCLUDED.total_volume,
  short_ratio  = EXCLUDED.short_ratio
"""


def pg_conn():
    return psycopg2.connect(
        host=os.environ.get("PGHOST_HOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT_HOST", "5434")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def num(v, cast=float, default=None):
    """KIS 응답은 콤마 없는 문자열이며 빈 문자열이 섞인다."""
    if v is None:
        return default
    s = str(v).strip().replace(",", "")
    if not s or s in {"-", "null"}:
        return default
    try:
        return cast(float(s))
    except (TypeError, ValueError):
        return default


def load_progress():
    try:
        with open(PROGRESS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_progress(p):
    os.makedirs(os.path.dirname(os.path.abspath(PROGRESS_PATH)), exist_ok=True)
    tmp = PROGRESS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PROGRESS_PATH)


def latest_trade_date(conn) -> date:
    cur = conn.cursor()
    cur.execute("SELECT MAX(trade_date) FROM market_data")
    mx = cur.fetchone()[0]
    cur.close()
    return mx or date.today()


def universe(conn, limit: int):
    """유동성(최근 60거래일 평균 거래대금) 상위 종목.

    함정 회피: `market_data` 를 스코프로만 삼으면 빈 DB 에서 0건이 된다 →
    비어 있으면 `stocks` 마스터로 폴백한다(스킬 규칙).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT s.stock_code, s.stock_name
        FROM stocks s
        JOIN (
            SELECT m.stock_code, AVG(m.trading_value) AS avg_tv
            FROM market_data m
            WHERE m.trade_date >= (SELECT MAX(trade_date) FROM market_data) - INTERVAL '95 days'
              AND m.trading_value IS NOT NULL AND m.volume > 0
              AND m.stock_code ~ '^[0-9]{6}$'
            GROUP BY m.stock_code
        ) liq ON liq.stock_code = s.stock_code
        WHERE COALESCE(s.instrument_type, 'STOCK') = 'STOCK'
        ORDER BY liq.avg_tv DESC NULLS LAST, s.stock_code
    """)
    rows = cur.fetchall()
    if not rows:
        log.warning("market_data 기반 유동성 조회 0건 — stocks 마스터로 폴백")
        cur.execute("""
            SELECT stock_code, stock_name FROM stocks
            WHERE COALESCE(instrument_type, 'STOCK') = 'STOCK'
              AND stock_code ~ '^[0-9]{6}$'
            ORDER BY market_cap DESC NULLS LAST, stock_code
        """)
        rows = cur.fetchall()
    cur.close()
    return rows[:limit] if limit and limit > 0 else rows


def fetch_one(client, code, start, end):
    """1종목 = 1콜. 응답 output2(거래일 내림차순) → 오름차순 정렬."""
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
              "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end}
    data = client._request(SHORT_SALE_PATH, SHORT_SALE_TR_ID, params)
    rows = data.get("output2") or []
    out = []
    for r in rows:
        d = str(r.get("stck_bsop_date") or "").strip()
        if len(d) != 8 or not d.isdigit():
            continue
        trade_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        sv = num(r.get("ssts_cntg_qty"), int)
        tv = num(r.get("acml_vol"), int)
        out.append((
            trade_date, code,
            sv or 0,
            num(r.get("ssts_tr_pbmn"), int) or 0,
            tv or 0,
            num(r.get("ssts_vol_rlim"), float) or 0.0,
        ))
    out.sort(key=lambda t: t[0])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30,
                    help="대상 종목 수 (0 = 전체 유니버스)")
    ap.add_argument("--calendar-days", type=int, default=120,
                    help="오늘 기준 조회 창(달력일). 120일 ≈ 80거래일")
    ap.add_argument("--start", help="YYYYMMDD (지정 시 --calendar-days 무시)")
    ap.add_argument("--end", help="YYYYMMDD (기본: market_data 최신 거래일)")
    ap.add_argument("--max-calls", type=int,
                    default=int(os.environ.get("KIS_MAX_CALLS", "600")))
    ap.add_argument("--delay", type=float,
                    default=float(os.environ.get("KIS_REQUEST_DELAY", "3.0")))
    ap.add_argument("--jitter", type=float,
                    default=float(os.environ.get("KIS_REQUEST_JITTER", "0.5")))
    ap.add_argument("--dry-run", action="store_true",
                    help="대상/구간만 출력하고 호출·쓰기 없음")
    ap.add_argument("--ignore-progress", action="store_true")
    args = ap.parse_args()

    appkey = os.environ.get("KIS_APP_KEY")
    appsecret = os.environ.get("KIS_APP_SECRET")
    if not appkey or not appsecret:
        log.error("KIS_APP_KEY/KIS_APP_SECRET 미설정 — "
                  "set -a && . ./.env && set +a 후 실행하세요")
        return 2

    conn = pg_conn()
    end_d = latest_trade_date(conn)
    if args.end:
        end_d = date(int(args.end[:4]), int(args.end[4:6]), int(args.end[6:]))
    if args.start:
        start_d = date(int(args.start[:4]), int(args.start[4:6]), int(args.start[6:]))
    else:
        start_d = end_d - timedelta(days=args.calendar_days)
    start_s, end_s = start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")

    targets = universe(conn, args.limit)
    log.info("대상 %d종목 / 구간 %s ~ %s / 호출 상한 %d",
             len(targets), start_s, end_s, args.max_calls)
    if args.dry_run:
        for c, n in targets:
            print(f"  {c} {n}")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM krx_short_selling")
        print(f"현재 krx_short_selling 행수: {cur.fetchone()[0]}")
        cur.close()
        conn.close()
        return 0

    base = os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    client = KisClient(appkey, appsecret, base,
                       token_path=os.environ.get(
                           "KIS_TOKEN_PATH",
                           os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "data", "kis", "token_cache.json")),
                       delay=0.0, jitter=0.0, retry_max=0)

    progress = {} if args.ignore_progress else load_progress()
    calls = 0
    total_rows = 0
    ok_stocks = 0
    failed = []
    skipped = 0
    cur = conn.cursor()

    for code, name in targets:
        key = f"{code}:{start_s}:{end_s}"
        if progress.get(key):
            skipped += 1
            continue
        if calls >= args.max_calls:
            log.warning("호출 상한(%d) 도달 — 진행파일 저장 후 중단", args.max_calls)
            break

        if calls:
            time.sleep(args.delay + random.uniform(0, args.jitter))
        calls += 1
        try:
            rows = fetch_one(client, code, start_s, end_s)
        except KisApiError as e:
            if e.credential_error:
                log.error("KIS 자격증명 거부(%s: %s) — 재시도 없이 즉시 종료",
                          e.msg_cd, e.msg1)
                failed.append((code, f"{e.msg_cd} {e.msg1}"))
                break
            log.warning("%s 실패: [%s] %s (http=%s)", code, e.msg_cd, e.msg1, e.http_status)
            failed.append((code, f"{e.msg_cd} {e.msg1}"))
            continue
        except KisTransportError as e:
            log.warning("%s 전송 오류: %s", code, e)
            failed.append((code, str(e)[:120]))
            continue

        if not rows:
            log.warning("%s: 응답 0행 (신규상장/거래정지 가능)", code)
            progress[key] = {"rows": 0, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            continue

        payload = [(r[0], r[1], name, r[2], r[3], r[4], r[5]) for r in rows]
        psycopg2.extras.execute_values(cur, UPSERT, payload, page_size=500)
        conn.commit()
        total_rows += len(payload)
        ok_stocks += 1
        progress[key] = {"rows": len(payload),
                         "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if ok_stocks % 10 == 0:
            save_progress(progress)
            log.info("진행 %d종목 / %d행 적재", ok_stocks, total_rows)

    save_progress(progress)
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code), "
                "MIN(trade_date), MAX(trade_date), "
                "SUM(CASE WHEN total_volume > 0 THEN 1 ELSE 0 END) "
                "FROM krx_short_selling")
    cnt, codes, mn, mx, withtv = cur.fetchone()
    cur.close()
    conn.close()

    log.info("=" * 62)
    log.info("완료: 성공 %d종목 / 적재 %d행 / 건너뜀(진행파일) %d / 실패 %d / 호출 %d",
             ok_stocks, total_rows, skipped, len(failed), calls)
    log.info("테이블 현황: %d행, %d종목, 기간 %s ~ %s, total_volume>0 인 행 %d",
             cnt, codes, mn, mx, withtv)
    for c, m in failed[:10]:
        log.info("  실패 %s: %s", c, m)
    if failed:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
