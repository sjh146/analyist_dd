#!/usr/bin/env python3
"""저녁 파이프라인(20:00) 이후 수집·예측 검증 — 이상할 때만 출력한다(watchdog).

  python3 scripts/nightly_verify.py                 # 오늘(거래일) 기준
  python3 scripts/nightly_verify.py --date 2026-11-12
  python3 scripts/nightly_verify.py --verbose       # 정상일 때도 요약 출력

검사 항목 (날짜 = KST 거래일)
  market_data(일봉) · minute_bars(분봉) · ml_predictions · supply_market_features ·
  krx_short_selling · krx_program_trading · news_analysis(최근 24h) · 피드 발행 ·
  컨테이너 기동 수
종료코드: 0 정상 / 3 이상(크론이 그대로 경보로 쓴다).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys

DB = "stock_postgres"
PG = ["docker", "exec", DB, "psql", "-U", "stock_user", "-d", "stock_trading", "-t", "-A", "-F", "|", "-c"]
CURL = os.environ.get("CURL_EXE", "/mnt/c/Windows/System32/curl.exe")
FEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "feed", "screener_latest.json")
EXPECT = {
    "market_data": 3000,
    "minute_bars": 1000,
    "ml_predictions": 3000,
    "supply_market_features": 1000,
    "krx_short_selling": 1,
    "krx_program_trading": 1,
}


def psql(sql: str) -> list:
    try:
        out = subprocess.run(PG + [sql], capture_output=True, text=True, timeout=90, errors="replace")
    except Exception:
        return []
    rows = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line:
            rows.append(line.split("|"))
    return rows


def counts_for(day: str) -> dict:
    sql = ("select 'market_data', count(*) from market_data where trade_date='{d}' "
           "union all select 'minute_bars', count(*) from minute_bars where trade_date='{d}' "
           "union all select 'ml_predictions', count(*) from ml_predictions where prediction_date='{d}' "
           "union all select 'supply_market_features', count(*) from supply_market_features where trade_date='{d}' "
           "union all select 'krx_short_selling', count(*) from krx_short_selling where trade_date='{d}' "
           "union all select 'krx_program_trading', count(*) from krx_program_trading where trade_date='{d}'")
    got = {}
    for row in psql(sql.format(d=day)):
        if len(row) >= 2:
            try:
                got[row[0]] = int(row[1])
            except ValueError:
                pass
    return got


def news_last_24h() -> int:
    rows = psql("select count(*) from news_analysis where analyzed_at > now() - interval '24 hours'")
    try:
        return int(rows[0][0])
    except (IndexError, ValueError):
        return -1


def containers_up() -> int:
    try:
        out = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=60)
        return len([x for x in (out.stdout or "").split() if x])
    except Exception:
        return -1


def feed_day() -> str:
    try:
        with open(FEED, encoding="utf-8") as handle:
            return str(json.load(handle).get("generated_at") or "")[:10]
    except Exception:
        return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nightly_verify.py", description=__doc__)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘 KST)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    day = args.date or datetime.datetime.now().strftime("%Y-%m-%d")
    problems = []
    got = counts_for(day)
    for table, minimum in EXPECT.items():
        n = got.get(table, -1)
        if n < minimum:
            problems.append("{0} {1}행 (기대 ≥ {2}) — {3}".format(table, n, minimum, day))
    news = news_last_24h()
    if news < 100:
        problems.append("news_analysis 최근 24h {0}건 (기대 ≥ 100)".format(news))
    up = containers_up()
    if 0 <= up < 15:
        problems.append("기동 컨테이너 {0}개 (기대 ≥ 15)".format(up))
    feed = feed_day()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    if day == today and feed != day:
        # 과거 날짜 검증(--date)에서는 오늘 피드와 비교하지 않는다(거짓 경보).
        problems.append("피드 generated_at {0} (기대 {1})".format(feed or "없음", day))

    if problems:
        print("[NIGHTLY] {0} 검증 실패 {1}건".format(day, len(problems)))
        for item in problems:
            print("  - {0}".format(item))
        return 3
    if args.verbose:
        print("[NIGHTLY OK] {0} · {1} · 컨테이너 {2} · 뉴스24h {3} · 피드 {4}".format(
            day, got, up, news, feed or "없음"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
