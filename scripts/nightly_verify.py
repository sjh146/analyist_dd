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
import hashlib
import glob
import json
import os
import subprocess
import sys

DB = "stock_postgres"
PG = ["docker", "exec", DB, "psql", "-U", "stock_user", "-d", "stock_trading", "-t", "-A", "-F", "|", "-c"]
CURL = os.environ.get("CURL_EXE", "/mnt/c/Windows/System32/curl.exe")
FEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "feed", "screener_latest.json")
# 지연 소스(일봉·수급·공매도·프로그램·분봉)는 D-1 기준으로 본다.
#   KRX OpenAPI 는 D-1/D-2 발행 지연, KIS 수급/공매도는 익일 반영, 분봉은 23:00 적재다.
#   당일 기준으로 재면 매일 거짓 경보가 난다(2026-11-13 실측으로 확인).
DELAYED = {
    "market_data": 3000,
    "minute_bars": 1000,
    "supply_market_features": 1000,
    "krx_short_selling": 1,
    "krx_program_trading": 1,
}
SAME_DAY = {"ml_predictions": 3000}


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


def prev_trading_day(day: str) -> str:
    """주말·휴장일을 건너뛴 직전 거래일."""
    import datetime as _dt

    holidays = set()
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "data", "krx_holidays.json"), encoding="utf-8") as handle:
            holidays = {str(x)[:10].replace("-", "") for x in json.load(handle)}
    except Exception:
        holidays = set()
    cursor = _dt.date.fromisoformat(day) - _dt.timedelta(days=1)
    for _ in range(10):
        if cursor.weekday() < 5 and cursor.strftime("%Y%m%d") not in holidays:
            return cursor.isoformat()
        cursor -= _dt.timedelta(days=1)
    return ( _dt.date.fromisoformat(day) - _dt.timedelta(days=1)).isoformat()


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


STATE = os.path.expanduser("~/.hermes/state/nightly_verify.json")


def already_reported(day: str, problems) -> bool:
    """같은 날 같은 문제 집합을 이미 보고했으면 조용히 넘어간다.

    크론이 하루에 두 번(22:20·23:20) 도는데, 첫 실행이 경보하고 두 번째가 같은 경보를
    한 번 더 보내면 소음이 된다. 파이프라인이 늦게 끝나는 날은 첫 실행이 보류되므로
    두 번 거는 것이 맞다.
    """
    sig = hashlib.sha1(("{0}|{1}".format(day, ";".join(sorted(problems)))).encode()).hexdigest()[:12]
    try:
        with open(STATE, encoding="utf-8") as handle:
            previous = json.load(handle)
    except Exception:
        previous = {}
    if previous.get("day") == day and previous.get("sig") == sig:
        return True
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as handle:
            json.dump({"day": day, "sig": sig}, handle, ensure_ascii=False)
    except OSError:
        pass
    return False


def pipeline_state(day: str) -> str:
    """20:00 저녁 파이프라인 상태 — 'finished' | 'running' | 'failed' | 'missing'.

    실측(2026-11-13): 파이프라인은 20:00 에 시작해 21:54~22:0x 에 끝난다(전일 21:54 완료).
    그래서 20:20 검증은 파이프라인 **도중**에 돌아 "예측 0행" 거짓 경보를 냈다.
    지금 상태를 보고, 진행 중이면 검증을 보류한다.
    """
    stamp = day.replace("-", "")
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
    logs = sorted(glob.glob(os.path.join(log_dir, "full_pipeline_dd_{0}_*.log".format(stamp))))
    log = logs[-1] if logs else None
    if log:
        try:
            with open(log, "rb") as handle:
                handle.seek(max(0, os.path.getsize(log) - 4000))
                if "Pipeline finished" in handle.read().decode("utf-8", "replace"):
                    return "finished"
        except OSError:
            pass
    # 과거·미래 날짜 검증(--date)에서는 오늘 도는 프로세스를 '그 날의 진행'으로 오독하지 않는다.
    if day == datetime.datetime.now().strftime("%Y-%m-%d"):
        try:
            if subprocess.run(["pgrep", "-f", "full_pipeline_dd"], capture_output=True).returncode == 0:
                return "running"
        except Exception:
            pass
    return "failed" if log else "missing"


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
    prev = prev_trading_day(day)
    problems = []
    got_today = counts_for(day)
    got_prev = counts_for(prev)
    for table, minimum in SAME_DAY.items():
        n = got_today.get(table, -1)
        if n < minimum:
            problems.append("{0} {1}행 (기대 ≥ {2}) — {3}".format(table, n, minimum, day))
    for table, minimum in DELAYED.items():
        n = got_prev.get(table, -1)
        if n < minimum:
            problems.append("{0} {1}행 (기대 ≥ {2}) — 직전 거래일 {3}".format(table, n, minimum, prev))
    state = pipeline_state(day)
    if state == "running":
        # 파이프라인이 아직 도는 중이면 예측·리포트는 아직 안 나온 것이 정상이다.
        if args.verbose:
            print("[NIGHTLY DEFER] {0} — 20:00 파이프라인 진행 중(예측 검증 보류)".format(day))
        return 0
    if state in ("failed", "missing"):
        problems.append("20:00 파이프라인 미완료({0}) — reports/full_pipeline_dd_{1}_*.log 확인".format(
            state, day.replace("-", "")))

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
        if already_reported(day, problems):
            return 3  # 이미 같은 경보를 보냈다 — 출력하지 않는다(래퍼가 조용히 넘김)
        print("[NIGHTLY] {0} 검증 실패 {1}건".format(day, len(problems)))
        for item in problems:
            print("  - {0}".format(item))
        return 3
    if args.verbose:
        print("[NIGHTLY OK] {0} 당일 {1} / 직전 거래일 {2} {3} · 컨테이너 {4} · 뉴스24h {5} · 피드 {6} · 파이프라인 {7}".format(
            day, got_today, prev, got_prev, up, news, feed or "없음", state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
