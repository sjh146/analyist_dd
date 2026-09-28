#!/usr/bin/env python3
"""수급 최신일 지연 상시 감시 프로브 (읽기 전용 — KIS 호출 0회, DB SELECT 3회).

WHY (2026-09-29): R19 는 수급 러너(호출 ~700회 / 25분)를 다시 돌려 '지연 상한 ≤2거래일'을
확인했다. 그런데 목표가 충족되면(rc=0 + check 통과) 구동기가 항목을 `done` 으로 적어
**감시가 사라진다** — 회전을 멈춘 주체를 아무도 못 본다. 반대로 감시를 위해 매 틱 러너를
다시 돌리면 KIS 호출 예산을 모니터링이 태운다(일일 한도 미확인 = R20 승인 항목).
→ 감시는 **싼 읽기 전용 프로브**로 분리한다: 수집은 하루 1회 크론(kis_supply.sh, 16:20),
감시는 이 스크립트(2시간 틱, DB 쿼리 3회), 판정은 백로그 check(같은 SQL).

판정 모집단 주의: '시장 최신일'이 아니라 **종목별 자기 시세의 최신일** 대비로 센다.
거래정지 종목(예: 008290 — market_data·수급 모두 2026-09-18 정지)은 수급이 '마땅히
있어야 하는데 없는' 것이 아니므로 지연에 세지 않는다. 시장 최신일 기준으로 세면 그 1종목이
MAX 를 영구히 4거래일로 고정해 '고쳐도 통과 못 하는 check' 가 된다(실측 2026-09-29 04:0x).

사용:
  cd /home/jhshi/analyist_dd && POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 \
    /usr/bin/python3 scripts/r21_supply_lag_probe.py
종료코드: 0 정상 조회 / 1 DB 오류(러너 환경 문제 — 조용히 넘기면 감시가 실명한다).
"""
import os
import sys

import psycopg2

PG = {
    "host": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    "port": int(os.environ.get("POSTGRES_PORT", "5434")),
    "user": os.environ.get("POSTGRES_USER", "stock_user"),
    "password": os.environ.get("POSTGRES_PASSWORD", ""),
    "dbname": os.environ.get("POSTGRES_DB", "stock_trading"),
}

LAG_SQL = """
WITH fi AS MATERIALIZED (
    SELECT stock_code, MAX(trade_date) AS d FROM foreign_institutional GROUP BY 1
)
SELECT COALESCE(MAX(l), 0) AS max_lag, COUNT(*) FILTER (WHERE l > 0) AS behind
FROM (
    SELECT m.stock_code, COUNT(*) AS l
    FROM market_data m
    JOIN fi ON fi.stock_code = m.stock_code AND m.trade_date > fi.d
    GROUP BY 1
) t
"""


def main():
    try:
        conn = psycopg2.connect(**PG)
    except Exception as exc:  # noqa: BLE001 - 조회 실패는 실명이므로 rc=1 로 크게 남긴다
        print(f"DB 연결 실패: {type(exc).__name__}: {exc}", flush=True)
        return 1
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM market_data")
        as_of = cur.fetchone()[0]
        cur.execute(LAG_SQL)
        max_lag, behind = cur.fetchone()
        cur.execute("""SELECT COUNT(DISTINCT stock_code), COUNT(*) FROM foreign_institutional""")
        stocks, rows = cur.fetchone()
        cur.execute("""SELECT COUNT(DISTINCT stock_code) FROM foreign_institutional
                       WHERE trade_date = %s""", (as_of,))
        newest = cur.fetchone()[0]
        cur.close()
    finally:
        conn.close()

    print(f"수급 지연 프로브(읽기전용) — 기준 거래일(시장) {as_of}")
    print(f"· 종목별 자기 시세 대비 지연: 최대 {max_lag}거래일 / 뒤처진 종목 {behind}개")
    print(f"· 최신일({as_of}) 수급 보유 {newest}종목 / 테이블 {stocks}종목 {rows:,}행")
    print(f"판정: {'OK' if max_lag <= 2 else '미달'} (최대 지연 {max_lag} <= 2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
