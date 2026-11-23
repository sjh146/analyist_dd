#!/usr/bin/env python3
"""일봉/분봉 데이터 감사 — 커버리지와 품질을 한 번에 확인한다.

  python3 scripts/verify_market_data.py [--root /home/dduckbeagy/analyist_dd]

보는 것:
  1) 월별 커버리지(거래일 수, 행수, 일평균 종목수)
  2) 기대 거래일 vs 적재 거래일 (휴장 파일 제외) — 공실 수치
  3) 부분 적재일(행수 < --thin)
  4) 무거래 행(시가·고가·저가 = 0) — 피처 계산 전 제거 대상
  5) OHLC 동결 종목(직전 거래일과 O/H/L/C 완전 동일) — 소스 품질 이상 신호
  6) 분봉 요약

설계 메모:
  - 호스트에 psql이 없어도 되도록 컨테이너에 붙는다. docker 그룹이 현재 셸에 없으면 `sg docker -c`로 재시도.
  - 휴장은 data/krx_holidays.json. 없으면 휴장 제외 없이 계산하고 경고를 찍는다.
  - SELECT만 실행한다(쓰기 없음).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta


Q_MONTHLY = """
SELECT to_char(trade_date,'YYYY-MM') AS mon, count(DISTINCT trade_date) AS days,
       count(*) AS rows, round(count(*)::numeric/count(DISTINCT trade_date)) AS per_day
FROM market_data GROUP BY 1 ORDER BY 1;
"""
Q_TOTAL = """
SELECT count(*) AS rows, count(DISTINCT trade_date) AS days,
       min(trade_date) AS d_min, max(trade_date) AS d_max FROM market_data;
"""
Q_THIN = """
SELECT trade_date, count(*) AS n FROM market_data GROUP BY 1
HAVING count(*) < %(thin)s ORDER BY 1;
"""
Q_ZERO = """
SELECT count(*) AS zero_rows, count(DISTINCT stock_code) AS stocks
FROM market_data WHERE open_price=0 AND high_price=0 AND low_price=0;
SELECT trade_date,
       count(*) FILTER (WHERE open_price=0 AND high_price=0 AND low_price=0) AS zero_ohlc,
       count(*) AS n
FROM market_data GROUP BY 1 ORDER BY 1 DESC LIMIT 5;
"""
Q_FLAT = """
WITH w AS (
  SELECT stock_code, trade_date, open_price, high_price, low_price, close_price,
         lag(open_price) OVER p AS po, lag(high_price) OVER p AS ph,
         lag(low_price) OVER p AS pl, lag(close_price) OVER p AS pc
  FROM market_data
  WHERE trade_date >= (SELECT max(trade_date) FROM market_data) - make_interval(days => %(days)s)
  WINDOW p AS (PARTITION BY stock_code ORDER BY trade_date)
), f AS (
  SELECT stock_code,
         count(*) FILTER (WHERE open_price=po AND high_price=ph
                            AND low_price=pl AND close_price=pc) AS flats,
         count(*) AS days
  FROM w WHERE pc IS NOT NULL GROUP BY 1
)
SELECT stock_code, flats, days FROM f WHERE flats >= 5 ORDER BY flats DESC LIMIT 15;
"""
Q_MINUTE = """
SELECT count(*) AS bars, count(DISTINCT stock_code) AS syms,
       min(trade_date) AS d_min, max(trade_date) AS d_max FROM minute_bars;
"""


def run_sql(sql, db, container):
    """컨테이너 psql로 SELECT 실행 → stdout+stderr 문자열."""
    inner = "docker exec -i {c} psql -U stock_user -d {d} -f -".format(c=container, d=db)
    cmd = ["sh", "-c", inner] if shutil.which("docker") else ["sg", "docker", "-c", inner]
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    out = r.stdout + r.stderr
    if "permission denied" in out and cmd[0] != "sg":
        r = subprocess.run(["sg", "docker", "-c", inner], input=sql,
                           capture_output=True, text=True)
        out = r.stdout + r.stderr
    return out


def expected_trading_days(d_min, d_max, holidays):
    """평일 − 휴장 파일 = 기대 거래일 수."""
    d, n = d_min, 0
    while d <= d_max:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            n += 1
        d += timedelta(days=1)
    return n


def parse_total_row(totals):
    """psql 표에서 합계 행 하나를 뽑는다 → (rows, days, d_min, d_max) 또는 None."""
    for line in totals.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) == 4 and cells[0].isdigit() and cells[1].isdigit():
            try:
                return int(cells[0]), int(cells[1]), cells[2], cells[3]
            except ValueError:
                return None
    return None


def main():
    ap = argparse.ArgumentParser(description="일봉/분봉 데이터 감사 (읽기 전용)")
    ap.add_argument("--root", default="/home/dduckbeagy/analyist_dd",
                    help="휴장 파일이 있는 레포 루트 (기본: 프로덕션 경로)")
    ap.add_argument("--db", default="stock_trading")
    ap.add_argument("--container", default="stock_postgres")
    ap.add_argument("--thin", type=int, default=2000, help="부분 적재로 볼 행수 임계(기본 2000)")
    ap.add_argument("--span-days", type=int, default=30, help="OHLC 동결 판정 기간(기본 30일)")
    args = ap.parse_args()

    hol_path = os.path.join(args.root, "data", "krx_holidays.json")
    try:
        holidays = set(json.load(open(hol_path, encoding="utf-8")))
    except (OSError, ValueError):
        holidays = set()
        print("WARN 휴장 파일 없음(%s) — 휴장 제외 없이 계산한다" % hol_path)

    print("=== 시장 데이터 월별 커버리지 ===")
    print(run_sql(Q_MONTHLY, args.db, args.container).strip())

    print("\n=== 합계 및 공실 ===")
    totals = run_sql(Q_TOTAL, args.db, args.container).strip()
    print(totals)
    parsed = parse_total_row(totals)
    if parsed:
        _rows, days, d_min, d_max = parsed
        try:
            exp = expected_trading_days(date.fromisoformat(d_min),
                                        date.fromisoformat(d_max), holidays)
            print("기대 거래일 %d vs 적재 %d / 공실 %d (휴장 %d일 제외, %s ~ %s)"
                  % (exp, days, exp - days, len(holidays), d_min, d_max))
            if exp != days:
                print("  → 결측 평일을 1일 2콜 프로브로 휴장/공실 분류할 것(휴장 오기록도 여기서 드러난다)")
        except ValueError:
            pass

    print("\n=== 부분 적재일 (행수 < %d) ===" % args.thin)
    print(run_sql(Q_THIN % {"thin": args.thin}, args.db, args.container).strip())

    print("\n=== 무거래 행 (시가·고가·저가 = 0) — 피처 계산 전 제거/플래그 대상 ===")
    print(run_sql(Q_ZERO, args.db, args.container).strip())

    print("\n=== OHLC 동결 종목 (최근 %d일, 동결 5일 이상) ===" % args.span_days)
    print(run_sql(Q_FLAT % {"days": args.span_days}, args.db, args.container).strip())

    print("\n=== minute_bars ===")
    print(run_sql(Q_MINUTE, args.db, args.container).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
