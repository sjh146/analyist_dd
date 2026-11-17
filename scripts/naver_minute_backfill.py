#!/usr/bin/env python3
"""Naver 분봉 종가 백필 — 신규 테이블 minute_close_bars (docs/spec_minute_replay.md v3 §1).

WHY (스펙 인용 + 실측 — 2026-11-18):
  자본 제약 시뮬(data/reports/capital_sim.json)의 일봉 애매(worst≠best) 문제를 풀려면 분 단위
  실제 경로가 필요하다. DB `minute_bars` 는 2026-09-23~11-17, 300종목/일, 하루 30행(15:01~15:30)
  뿐이다(KIS 수집기 페이지 종료 조건 버그 — 스펙 실측). KIS 는 당일 데이터만 주므로 과거 복구가
  불가능하고, Naver 분봉 API 만 과거 다일자를 준다. 이 스크립트는 Naver 응답의 **종가·누적거래량만**
  신규 테이블에 적재한다(OHLC 는 null 이라 저장하지 않음 — 없는 값을 만들지 않는다, 스펙 금지).

실측 조회 명령(2026-11-18 실행, 접속은 이 프로세스 환경변수 — 비밀번호 출력 금지):
  -- SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM minute_bars;
  -- → 2026-09-23 ~ 2026-11-17 · 127,290행 (스펙 실측 '하루 30행뿐' 재확인)
  -- SELECT COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date)
  --   FROM market_data WHERE trade_date BETWEEN '2026-09-23' AND '2026-11-17';
  -- → 36거래일 (요청 창 분할 기준)
  -- curl --noproxy '*' 'https://api.finance.naver.com/siseJson.naver?symbol=005930\
  --   &requestType=1&startTime=20261110&endTime=20261117&timeframe=minute'
  -- → 200 OK · 238,348B · 데이터행 3,808 = 6거래일 × 정규장(09:00~15:30) 381행/일(스펙 실측과 일치)
  --   · 행 형식 ["YYYYMMDDHHMM", null, null, null, 종가, 누적거래량, null] — OHLC null
  --   · 헤더행(날짜,시가,고가,저가,종가,거래량,외국인소진율)만 홑따옴표, 데이터행은 쌍따옴표
  --     → "'"→'"' 치환 후 json.loads 로 파싱(이 치환 없이는 JSONDecodeError 실측)
  --   · 행은 시간 내림차순 · 시간외(15:31~19:59) 1,522행 포함 → 정규장 09:00~15:30 필터 필수
  --   · 정규장 행의 종가/누적거래량 null 0건 · (날짜,시간) 중복 0건(2026-11-18 실측)
  -- curl --noproxy '*' '...startTime=20260923&endTime=20261002...' → 헤더만(빈 응답 70B, EUC-KR)
  -- curl --noproxy '*' '...startTime=20261105&endTime=20261110...' → 20261110 하루만
  -- → API 는 **최근 ~6거래일만** 제공(2026-11-18 시점 11-10~11-17). 스펙 '안 되면 받은 만큼만
  --   쓴다' 에 따라 과거 창이 비어도 빈 채로 두고 종목 루프는 계속한다(부분 성공 허용).

멱등: UPSERT(ON CONFLICT (stock_code, trade_date, time) DO NOTHING) — 재실행 시 신규 행 0.
자기신고: scripts/dq_claim.py 의 claim_start/claim_finish(record_claim) 재사용 —
  source_rows=API 가 준 데이터 행수, claimed_rows=파서가 만든 정규장 행수, persisted=테이블 델타.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parent.parent
SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import dq_claim  # noqa: E402 — record_claim(자기신고 3분리 — 스펙 ④)
import capital_sim as cs  # noqa: E402 — build_signals(신호 정의는 capital_sim 과 동일 — 스펙)
import edge_stack_backtest as esb  # noqa: E402 — load/compute_features(신호 종목 해석)

TABLE = "minute_close_bars"
SOURCE = "naver_sise_minute"
MAX_CODES = 400          # 대상 종목 상한(스펙: 신호 전체 중 최대 400종목)
REGULAR_MIN, REGULAR_MAX = "0900", "1530"   # 정규장 필터(실측: 하루 381분)

DDL = """
CREATE TABLE IF NOT EXISTS minute_close_bars (
    stock_code   TEXT                NOT NULL,
    trade_date   DATE                NOT NULL,
    time         TEXT                NOT NULL,
    close_price  DOUBLE PRECISION    NOT NULL,
    cum_volume   DOUBLE PRECISION,
    source       TEXT,
    fetched_at   TIMESTAMPTZ         NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_code, trade_date, time)
)
"""

UPSERT_SQL = (
    "INSERT INTO minute_close_bars (stock_code, trade_date, time, close_price,"
    " cum_volume, source) VALUES (%s, %s, %s, %s, %s, %s)"
    " ON CONFLICT (stock_code, trade_date, time) DO NOTHING"
)


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"), connect_timeout=15)


def trading_days(conn, start, end):
    """시장 거래일(market_data 기준, SELECT 전용) — 요청 창 분할·요약 기준."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT DISTINCT trade_date FROM market_data"
            " WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date", (start, end))
        return [r[0].isoformat() for r in cur.fetchall()]
    finally:
        cur.close()


def chunk_windows(days, window_days):
    """거래일 리스트를 window_days 거래일 창으로 분할(스펙: 5거래일 창 — 응답 크기·한도 방어)."""
    return [days[i:i + window_days] for i in range(0, len(days), window_days)]


def parse_response(text):
    """Naver siseJson 분봉 응답 파싱 — 종가·누적거래량만, 정규장(09:00~15:30)만.

    실측 형식(2026-11-18): 헤더행은 홑따옴표, 데이터행은 쌍따옴표 → "'"→'"' 치환 후 json 로드.
    행: ["YYYYMMDDHHMM", 시가, 고가, 저가, 종가, 누적거래량, 외국인소진율] — OHLC 는 null.
    반환: (raw_n, parsed). raw_n = 소스가 준 데이터 행수(자기신고 source 용 — 필터 전),
    parsed = [(trade_date, time, close, cum_volume)] 오름차순 정렬.
    """
    try:
        data = json.loads(text.replace("'", '"'))
    except (json.JSONDecodeError, ValueError):
        return 0, []
    raw_n, parsed = 0, []
    for r in data:
        if not (isinstance(r, list) and r and isinstance(r[0], str)
                and len(r[0]) == 12 and r[0].isdigit()):
            continue                              # 헤더행 등 스킵
        raw_n += 1
        hhmm = r[0][8:12]
        if not (REGULAR_MIN <= hhmm <= REGULAR_MAX):
            continue                              # 정규장 밖(장전·시간외) 제외
        close = r[4] if len(r) > 4 else None
        cum = r[5] if len(r) > 5 else None
        if not isinstance(close, (int, float)) or isinstance(close, bool) or close <= 0:
            continue                              # 종가 없는 행은 저장하지 않음(추정 금지)
        parsed.append(("%s-%s-%s" % (r[0][:4], r[0][4:6], r[0][6:8]), hhmm,
                       float(close), None if cum is None else float(cum)))
    parsed.sort()
    return raw_n, parsed


def store_rows(conn, rows):
    """UPSERT(멱등) — rows: [(stock_code, trade_date, time, close, cum_volume, source)].
    반환: 신규 삽입 행수(재실행 시 중복은 DO NOTHING → 0)."""
    cur = conn.cursor()
    try:
        cur.executemany(UPSERT_SQL, rows)
        n = int(cur.rowcount or 0)
        conn.commit()
    finally:
        cur.close()
    return n


def make_fetcher(opener, retry_sleep=1.0, timeout=30):
    """(종목, 창) → (raw_n, parsed). 프록시 무시 opener + 1회 재시도, 그래도 실패면 예외 위임."""
    def fetch(code, start_yyyymmdd, end_yyyymmdd):
        url = ("https://api.finance.naver.com/siseJson.naver?symbol=%s"
               "&requestType=1&startTime=%s&endTime=%s&timeframe=minute"
               % (code, start_yyyymmdd, end_yyyymmdd))
        last = None
        for _ in range(2):
            try:
                body = opener.open(url, timeout=timeout).read()
                try:
                    text = body.decode("utf-8")   # 데이터 응답은 UTF-8(실측)
                except UnicodeDecodeError:
                    text = body.decode("euc-kr")  # 빈 응답은 EUC-KR(실측)
                return parse_response(text)
            except Exception as exc:  # noqa: BLE001 — 부분 성공 허용(스펙)
                last = exc
                time.sleep(retry_sleep)
        raise last
    return fetch


def backfill_once(fetch, codes, windows, store, sleep=0.0, dry_run=False, log=None):
    """종목 × 창 순회. 실패·빈 응답은 로그 후 계속(스펙: 부분 성공 허용).

    fetch(code, s, e) → (raw_n, parsed) · store(rows) → 신규 행수(dry_run 이면 호출 안 함).
    반환 stats: codes, days, windows, rows_received(원천), rows_parsed(정규장),
                rows_written(신규), codes_empty(전 창 0행 종목 수).
    """
    def lg(msg):
        if log is not None:
            log(msg)

    stats = {"codes": len(codes), "days": sum(len(w) for w in windows),
             "windows": len(windows), "rows_received": 0, "rows_parsed": 0,
             "rows_written": 0, "codes_empty": 0}
    for code in codes:
        code_rows = 0
        for win in windows:
            s, e = win[0], win[-1]
            try:
                raw_n, rows = fetch(code, s.replace("-", ""), e.replace("-", ""))
            except Exception as exc:  # noqa: BLE001
                lg("[backfill] %s %s~%s 실패(%s: %s) — 계속"
                   % (code, s, e, type(exc).__name__, exc))
                if sleep:
                    time.sleep(sleep)
                continue
            stats["rows_received"] += raw_n
            stats["rows_parsed"] += len(rows)
            written = 0
            if rows and store is not None:
                written = store([(code, d, t, c, v, SOURCE) for d, t, c, v in rows])
                stats["rows_written"] += written
            code_rows += len(rows)
            lg("[backfill] %s %s~%s 원천 %d행 → 정규장 %d행(신규 %d행)"
               % (code, s, e, raw_n, len(rows), written))
            if sleep:
                time.sleep(sleep)
        if code_rows == 0:
            stats["codes_empty"] += 1
            lg("[backfill] %s: 전 창 빈 응답(0행)" % code)
    return stats


def resolve_codes(args):
    """대상 종목: 명시(--codes/--codes-file) 우선, 기본값은 신호 발생 종목 ≤ 400.

    신호 정의는 capital_sim 과 동일(build_signals import — 스펙). 자본 제약 우선순위 없음
    (스펙: '자본 제약으로 실제 체결 가능한 종목 우선 아님 — 신호 전체'). 종목코드 정렬로 고정(결정적).
    """
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.codes_file:
        with open(args.codes_file, encoding="utf-8") as f:
            codes = [ln.strip() for ln in f if ln.strip()]
    else:
        warm = (date.fromisoformat(args.from_) - timedelta(days=cs.WARMUP_DAYS)).isoformat()
        load_end = (date.fromisoformat(args.to) + timedelta(days=cs.BUFFER_DAYS)).isoformat()
        df = esb.load(warm, load_end)
        df = esb.compute_features(df)
        _raw_n, signals, _bars, _dates = cs.build_signals(df, args.from_, args.to)
        codes = sorted({s["stock"] for s in signals})
        print("[대상] 신호 발생 종목 %d개(신호 정의=capital_sim) → 상한 %d개"
              % (len(codes), MAX_CODES))
        codes = codes[:MAX_CODES]
    return codes


def create_table(conn):
    cur = conn.cursor()
    try:
        cur.execute(DDL)
        conn.commit()
    finally:
        cur.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Naver 분봉 종가 백필 (docs/spec_minute_replay.md §1)")
    ap.add_argument("--from", dest="from_", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--codes-file", default=None)
    ap.add_argument("--codes", default=None)
    ap.add_argument("--window-days", type=int, default=5)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.from_ >= args.to:
        ap.error("--from 은 --to 보다 앞이어야 합니다")

    conn = connect()
    days = trading_days(conn, args.from_, args.to)
    if not days:
        print("[backfill] 시장 거래일 없음: %s~%s (market_data)" % (args.from_, args.to))
        conn.close()
        return 1
    windows = chunk_windows(days, args.window_days)
    codes = resolve_codes(args)
    create_table(conn)
    conn.close()
    print("[backfill] 종목 %d개 × 거래일 %d일(%d개 창 · 창당 %d거래일) · dry-run=%s"
          % (len(codes), len(days), len(windows), args.window_days, args.dry_run))

    claimed = False
    if not args.dry_run:
        claimed = dq_claim.claim_start("naver_minute_backfill", TABLE,
                                       note="codes=%d days=%d" % (len(codes), len(days)))
    # 프록시 무시(ProxyHandler({})) — curl --noproxy '*' 와 동등. 네트워크는 Naver 시세 API 만.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.addheaders = [("User-Agent", "Mozilla/5.0 (analyist-dd naver_minute_backfill)")]
    fetch = make_fetcher(opener)
    conn2 = None if args.dry_run else connect()
    try:
        stats = backfill_once(
            fetch, codes, windows,
            None if args.dry_run else (lambda rows: store_rows(conn2, rows)),
            sleep=args.sleep, dry_run=args.dry_run, log=print)
    finally:
        if conn2 is not None:
            conn2.close()
    if claimed:
        dq_claim.claim_finish("naver_minute_backfill",
                              source_rows=stats["rows_received"],
                              claimed_rows=stats["rows_parsed"])
    if args.dry_run:
        print("[dry-run] 저장 없음 — rows_parsed=%d" % stats["rows_parsed"])
    # 스펙 요약줄(마지막 줄): codes=.., days=.., rows_written=.., codes_empty=..
    print("codes=%d, days=%d, rows_written=%d, codes_empty=%d"
          % (stats["codes"], stats["days"], stats["rows_written"], stats["codes_empty"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
