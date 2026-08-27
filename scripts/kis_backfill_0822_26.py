#!/usr/bin/env python3
"""KIS 08-22~26 백필 러너 — 전 종목 범위 조회 → market_data upsert.

- 1회 호출로 최대 100일치 일봉 (date_from~date_to)
- 5s 딜레이 + jitter (분당 쿼터 대비 여유)
- 진행 파일(data/kis/backfill_progress.txt)로 중단 지점 저장 → 재실행 시 이어서
- 일일 쿼터 소진 시(오류) 중단하고 다음 실행에서 이어감 (천천히 원칙)
"""
import os
import time
import random

import psycopg2
from kis_app.client.kis_client import KisClient
from kis_app.collectors.daily_collector import parse_daily_bars
from kis_app.config import Config

PG = dict(
    host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    port=int(os.environ.get("POSTGRES_PORT", "5434")),
    user=os.environ.get("POSTGRES_USER", "stock_user"),
    password=os.environ["POSTGRES_PASSWORD"],
    dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
)
DATE_FROM, DATE_TO = "20260822", "20260826"
PROGRESS = "data/kis/backfill_progress.txt"


def load_done():
    if not os.path.exists(PROGRESS):
        return set()
    return {l.strip() for l in open(PROGRESS) if l.strip()}


def save_done(code):
    with open(PROGRESS, "a") as f:
        f.write(code + "\n")


def main():
    cfg = Config()
    c = KisClient(
        os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"],
        cfg.KIS_BASE_URL, daily_tr_id=cfg.KIS_DAILY_TR_ID,
        delay=float(os.environ.get("KIS_REQUEST_DELAY", "5.0")), jitter=0.5,
    )
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.stock_code, s.market FROM stocks s
        WHERE s.market IN ('KOSPI', 'KOSDAQ')
        GROUP BY s.stock_code, s.market
    """)
    universe = [(r[0], "K" if str(r[1]).upper() == "KOSDAQ" else "J") for r in cur.fetchall()]
    cur.close()

    done = load_done()
    todo = [u for u in universe if u[0] not in done]
    print(f"전체 {len(universe)} / 완료 {len(done)} / 남은 {len(todo)}", flush=True)

    ok = fail = no_data = bars = 0
    for code, excd in todo:
        try:
            resp = c.get_daily_chart(code, excd, DATE_FROM, DATE_TO)
            rows = parse_daily_bars(resp)
            if rows:
                cur = conn.cursor()
                cur.executemany(
                    """
                    INSERT INTO market_data
                      (stock_code, trade_date, open_price, high_price, low_price,
                       close_price, volume, trading_value)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (stock_code, trade_date) DO UPDATE SET
                      open_price=EXCLUDED.open_price, high_price=EXCLUDED.high_price,
                      low_price=EXCLUDED.low_price, close_price=EXCLUDED.close_price,
                      volume=EXCLUDED.volume, trading_value=EXCLUDED.trading_value
                    """,
                    [
                        (code, r["trade_date"], r["open_price"], r["high_price"],
                         r["low_price"], r["close_price"], r["volume"], r.get("trading_value"))
                        for r in rows
                    ],
                )
                conn.commit()
                cur.close()
                bars += len(rows)
                ok += 1
                save_done(code)
            else:
                no_data += 1
                print(f"  [{code}] 봉 데이터 없음 (스킵, 재시도 가능)", flush=True)
        except Exception as e:
            fail += 1
            msg = str(e)[:100]
            print(f"  [{code}] 실패: {msg}", flush=True)
            if "quota" in msg.lower() or "EGW" in msg or "429" in msg or "초과" in msg:
                print("  → 쿼터/제한 감지 — 중단, 다음 실행에서 이어서 진행", flush=True)
                break
            time.sleep(10)

    print(f"완료: ok={ok} fail={fail} no_data={no_data} 바={bars} (누적 완료 {len(done) + ok})", flush=True)


if __name__ == "__main__":
    main()
