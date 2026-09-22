#!/usr/bin/env python3
"""KIS 기간 백필 러너 — 시작~종료일 구간을 전 종목 1회 호출로 채운다.

기존 `kis_backfill_0822_26.py`(고정 5일 구간)의 일반화 버전.
KIS 일봉 API는 **1회 호출로 최대 100일치**를 주므로, 공실이 며칠이든
종목당 1콜로 처리된다 (일자별 반복 대비 N배 절약).

사용 (호스트, services/kis-collector 기준):
  set -a; . ../../.env; set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  python3 ../../scripts/kis_backfill_range.py                       # 자동: DB 최신일+1 ~ 오늘
  python3 ../../scripts/kis_backfill_range.py --from 20260827 --to 20260922
  KIS_MAX_CALLS=2500 python3 ...                                    # 일일 호출 상한(기본 2500)

- 진행 파일(data/kis/backfill_range_<from>_<to>.txt)로 중단 지점 저장 → 같은 구간 재실행 시 이어서
- 일일 쿼터 소진/토큰 제한 감지 시 중단하고 다음 실행에서 이어감 (천천히 원칙)
"""
import argparse
import os
import random
import sys
import time
from datetime import date, datetime, timedelta

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "services", "kis-collector"))
from kis_app.client.kis_client import KisClient  # noqa: E402
from kis_app.collectors.daily_collector import parse_daily_bars  # noqa: E402
from kis_app.config import Config  # noqa: E402

PROJ = os.environ.get("PROJ_DIR", "/home/dduckbeagy/analyist_dd")
PG = dict(
    host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    port=int(os.environ.get("POSTGRES_PORT", "5434")),
    user=os.environ.get("POSTGRES_USER", "stock_user"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
    dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
)

UPSERT = """
INSERT INTO market_data
  (stock_code, trade_date, open_price, high_price, low_price,
   close_price, volume, trading_value)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
  open_price=EXCLUDED.open_price, high_price=EXCLUDED.high_price,
  low_price=EXCLUDED.low_price, close_price=EXCLUDED.close_price,
  volume=EXCLUDED.volume, trading_value=EXCLUDED.trading_value
"""


def default_range(conn):
    """DB 최신 거래일 +1 ~ 오늘 (없으면 최근 365일)."""
    cur = conn.cursor()
    cur.execute("SELECT MAX(trade_date) FROM market_data")
    mx = cur.fetchone()[0]
    cur.close()
    today = date.today()
    start = (mx + timedelta(days=1)) if mx else (today - timedelta(days=365))
    return start, today


def universe(conn):
    """stocks 테이블의 KOSPI/KOSDAQ 전 종목 (EXCD는 종목코드로 판별되므로 'J' 고정)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT s.stock_code FROM stocks s
        WHERE s.market IN ('KOSPI', 'KOSDAQ')
        GROUP BY s.stock_code ORDER BY s.stock_code
    """)
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    return codes


def progress_path(d_from, d_to):
    return os.path.join(PROJ, "data", "kis",
                        "backfill_range_{0}_{1}.txt".format(d_from, d_to))


def load_done(path):
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {l.strip() for l in f if l.strip()}


def save_done(path, code):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(code + "\n")


def main():
    ap = argparse.ArgumentParser(description="KIS 기간 백필 (전 종목, 종목당 1콜)")
    ap.add_argument("--from", dest="d_from", default=None, help="YYYYMMDD")
    ap.add_argument("--to", dest="d_to", default=None, help="YYYYMMDD")
    ap.add_argument("--limit", type=int, default=0, help="점검용 종목 수 제한(0=전체)")
    args = ap.parse_args()

    cfg = Config()
    if not cfg.KIS_APP_KEY or not cfg.KIS_APP_SECRET:
        print("KIS_APP_KEY/KIS_APP_SECRET 미설정 — .env 를 채운 뒤 다시 실행하세요.", flush=True)
        return 2

    conn = psycopg2.connect(**PG)
    if args.d_from and args.d_to:
        start, end = date.fromisoformat(args.d_from), date.fromisoformat(args.d_to)
    else:
        start, end = default_range(conn)
    d_from, d_to = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    if start > end:
        print("백필 필요 구간 없음: DB 최신일 {0} >= 종료일 {1}".format(start - timedelta(days=1), end), flush=True)
        return 0

    codes = universe(conn)
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        print("유니버스 없음 — stocks 테이블이 비었습니다 (scripts/init_stocks.py 먼저 실행).", flush=True)
        return 1

    ppath = progress_path(d_from, d_to)
    done = load_done(ppath)
    todo = [c for c in codes if c not in done]
    print("구간 {0}~{1} ({2}일) / 유니버스 {3}종목 / 완료 {4} / 남음 {5}".format(
        d_from, d_to, (end - start).days + 1, len(codes), len(done), len(todo)), flush=True)

    client = KisClient(
        cfg.KIS_APP_KEY, cfg.KIS_APP_SECRET, cfg.KIS_BASE_URL,
        daily_tr_id=cfg.KIS_DAILY_TR_ID, delay=float(os.environ.get("KIS_REQUEST_DELAY", "5.0")),
        jitter=0.5,
    )
    max_calls = int(os.environ.get("KIS_MAX_CALLS", "2500"))
    ok = fail = nodata = bars = 0
    calls = 0
    for code in todo:
        if calls >= max_calls:
            print("일일 상한({0}) 도달 — 중단, 다음 실행에서 이어서".format(max_calls), flush=True)
            break
        calls += 1
        try:
            resp = client.get_daily_chart(code, "J", d_from, d_to)
            rows = parse_daily_bars(resp)
            if rows:
                cur = conn.cursor()
                cur.executemany(UPSERT, [
                    (code, r["trade_date"], r["open_price"], r["high_price"],
                     r["low_price"], r["close_price"], r["volume"], r.get("trading_value"))
                    for r in rows
                ])
                conn.commit()
                cur.close()
                bars += len(rows)
                ok += 1
                save_done(ppath, code)
            else:
                nodata += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            msg = str(e)[:120]
            print("  [{0}] 실패: {1}".format(code, msg), flush=True)
            low = msg.lower()
            if "quota" in low or "egw" in low or "429" in low or "초과" in msg:
                print("  → 쿼터/제한 감지 — 중단, 다음 실행에서 이어서", flush=True)
                break
            time.sleep(10)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_date), MAX(trade_date) "
                "FROM market_data WHERE trade_date BETWEEN %s AND %s", (start, end))
    dist, total, mn, mx = cur.fetchone()
    cur.close()
    print("완료: ok={0} fail={1} no_data={2} 바={3} | DB: {4}종목 {5}행 ({6}~{7})".format(
        ok, fail, nodata, bars, dist, total, mn, mx), flush=True)
    conn.close()
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    random.seed()
    sys.exit(main())
