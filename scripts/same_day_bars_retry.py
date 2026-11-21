#!/usr/bin/env python3
"""R37 — 당일 일봉 적재의 이중 실패를 밤새 방치하지 않는다 (평일 22:35 재시도).

배경(실측 2026-11-11): 일봉은 두 경로가 같은 날 저녁에 적재한다 — 18:55 daily_bars
(KRX → 실패 시 KIS 폴백)와 20:00 full_pipeline phase 1-1(yfinance). 둘 다 실패하면
당일 봉이 통째로 비고, 다음 재시도는 **익일 04:15 data_gap 백필** 하나뿐이었다.
그 사이 모델·스크리너는 전일 데이터로 돈다. 실패는 어디에도 경보가 없었다:
timeout 으로 죽은 phase 는 자기신고 줄([claim])을 남기지 않고, data_gap.find_gaps() 는
당일을 구조적으로 제외한다(장 개시 전 오적재 방지).

이 러너는 그 사각지대만 메운다:
  1. 오늘이 거래일인지 확인(주말·data/krx_holidays.json 휴장 → 조용히 종료)
  2. 당일 market_data 행수를 직전 정상일 중앙값과 비교
  3. 60% 미만이면 naver 경로로 **그날만** 다시 받는다(1콜/종목, net_guard 경유)
  4. 재확인해 여전히 미달이면 exit 1(크론 로그·감시가 실패로 읽는다)

사용:
  python3 scripts/same_day_bars_retry.py            # 실제 재시도
  python3 scripts/same_day_bars_retry.py --dry-run  # 판정만(수집 없음)
  python3 scripts/same_day_bars_retry.py --date 2026-11-20 --dry-run
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import date, datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLIDAY_PATH = os.path.join(PROJ, "data", "krx_holidays.json")
BASELINE_DAYS = 5
MIN_RATIO = 0.60


def _pg(sql):
    try:
        import psycopg2  # type: ignore
    except ImportError:
        import psycopg  # type: ignore
        conn = psycopg.connect(
            host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.environ.get("POSTGRES_PORT", "5434")),
            dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
            user=os.environ.get("POSTGRES_USER", "stock_user"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
        )
        with conn, conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        conn.close()


def day_count(d):
    rows = _pg(
        "SELECT count(*) FROM market_data WHERE trade_date = DATE '%s'" % d
    )
    return int(rows[0][0]) if rows else 0


def baseline_counts(before_date):
    rows = _pg(
        "SELECT trade_date, count(*) FROM market_data "
        "WHERE trade_date < DATE '%s' GROUP BY 1 ORDER BY 1 DESC LIMIT %d"
        % (before_date, BASELINE_DAYS)
    )
    return [(str(r[0]), int(r[1])) for r in rows if int(r[1]) > 0]


def load_holidays():
    try:
        with open(HOLIDAY_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return set(d if isinstance(d, list) else d.get("days", []))
    except (OSError, ValueError):
        return set()


def main():
    ap = argparse.ArgumentParser(description="당일 일봉 이중 실패 재시도(R37)")
    ap.add_argument("--date", default=None, help="대상일 YYYY-MM-DD (기본 오늘)")
    ap.add_argument("--dry-run", action="store_true", help="판정만 하고 수집하지 않는다")
    ap.add_argument("--min-ratio", type=float, default=MIN_RATIO,
                    help=f"정상일 중앙값 대비 이 비율 미만이면 재시도 (기본 {MIN_RATIO})")
    ap.add_argument("--max-calls", type=int, default=4500, help="naver 재시도 콜 상한")
    args = ap.parse_args()

    ts = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    target = args.date or date.today().isoformat()
    y, m, d = (int(x) for x in target.split("-"))

    if date(y, m, d).weekday() >= 5:
        print(f"[{ts()}] {target} 주말 — 재시도 없음")
        return 0
    if target in load_holidays():
        print(f"[{ts()}] {target} 휴장(data/krx_holidays.json) — 재시도 없음")
        return 0

    today_n = day_count(target)
    base = baseline_counts(target)
    if not base:
        print(f"[{ts()}] {target} 기준선 없음(직전 정상일 0) — 판정 불가, 재시도 없음")
        return 0
    med = int(statistics.median([c for _, c in base]))
    ratio = today_n / med if med else 0.0
    print(
        f"[{ts()}] {target} 당일 행수 {today_n} / 직전 정상일 중앙값 {med} "
        f"= {ratio:.0%} (문턱 {args.min_ratio:.0%}, 기준선 {base[:3]})"
    )
    if ratio >= args.min_ratio:
        print(f"[{ts()}] 정상 — 재시도 없음")
        return 0

    if args.dry_run:
        print(f"[{ts()}] 미달이지만 --dry-run — 수집 생략(재시도 예정이었음)")
        return 0

    cmd = [
        sys.executable, os.path.join(PROJ, "scripts", "naver_daily_backfill.py"),
        "--start", target, "--end", target, "--max-calls", str(args.max_calls),
    ]
    print(f"[{ts()}] 재시도 실행: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=PROJ)
    after_n = day_count(target)
    print(
        f"[{ts()}] 재시도 후 {after_n}행 (rc={proc.returncode}) "
        f"= {after_n / med:.0%} (문턱 {args.min_ratio:.0%})"
    )
    if after_n / med >= args.min_ratio:
        return 0
    print(f"[{ts()}] FAIL 재시도 후에도 미달 — 익일 04:15 data_gap 백필까지 공백")
    return 1


if __name__ == "__main__":
    sys.exit(main())
