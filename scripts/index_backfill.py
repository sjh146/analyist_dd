#!/usr/bin/env python3
"""지수(KOSPI/KOSDAQ) 일봉 백필 — 신규 테이블 index_daily (docs/spec_index_backfill.md).

WHY (스펙 인용 + 실측 — 2026-11-20):
  etf_premium_analysis.py 의 by_regime 이 항상 null 이다. 그 코드의 load_index_change
  (scripts/etf_premium_analysis.py:320)는 당일 지수 등락을 market_data 에서
  stock_code IN ('KOSPI','KOSDAQ','1001','2001') 로 찾는데, DB 실측 조회(2026-11-20,
  접속은 프로세스 환경변수만 — 비밀번호 출력·기록 금지):
  -- SELECT COUNT(*) FROM market_data WHERE stock_code = ANY(ARRAY['KOSPI','KOSDAQ','1001','2001']);
  -- → 0행 (지수는 종목 테이블에 없다)
  -- SELECT DISTINCT indicator_name FROM macro_indicators ORDER BY 1;
  -- → 기준금리·국고채3년·회사채3년·CNY/JPY/USD KRW 환율·CPI·PPI·WTI 9종뿐(지수 없음)
  -- SELECT table_name FROM information_schema.tables
  --   WHERE table_schema='public' AND table_name LIKE '%index%';
  -- → 0개 테이블 (지수 일봉 테이블 자체가 없다)
  "패닉일 조건부" 판정(하락장에서도 돈이 되는가)을 하려면 실제 지수가 필요하다.

원천 실측 (2026-11-20, net_guard 경유 호출 — 연속 호출 간 1초 이상):
  -- https://api.finance.naver.com/siseJson.naver?symbol=KOSPI&requestType=1
  --   &startTime=20250101&endTime=20261120&timeframe=day
  -- → 200 OK · 30,250B · KOSPI 데이터행 459 · 20250102~20261120 (오름차순)
  -- · KOSDAQ 동일 창 459행 (첫 20250102 close 678.98, 끝 20261120 close 645.68)
  -- · 헤더행 ['날짜','시가',...] 은 홑따옴표, 데이터행은 쌍따옴표, 선두 '\n ' + 행간 탭
  -- · ast.literal_eval(원문) → IndentationError(unexpected indent) 실측 — .strip() 후 파싱해야 함
  -- · ast.literal_eval(strip) 과 json.loads("'"→'"' 치환) 모두 OK(실측 확인) — 스펙대로 literal_eval 사용
  -- · 결측값(null·빈 값·비수치) 실측: KOSPI 26년 6,627행·KOSDAQ 6,626행 모두 0건
  --   (일봉 OHLC 는 항상 존재 — 분봉과 달리 null 이 없다). 그래도 스펙 테스트 ③대로
  --   비수치 값이 온 행은 건너뛰고 카운트에 남긴다(방어적 — 데이터가 있으면 그대로).
  -- · 미래 창(2030) → 헤더행만 있는 빈 응답(0행, 정상)
  -- · 7번째 컬럼 '외국인소진율'은 DDL 에 없어 저장하지 않는다(스펙 DDL 그대로).

저장: 신규 테이블 index_daily 만(CREATE TABLE IF NOT EXISTS — 기존 테이블 무변경).
멱등: UPSERT(ON CONFLICT (index_code, trade_date) DO UPDATE) — 재실행 시 행수 불변,
  값은 최신으로 갱신(스펙: 'ON CONFLICT DO UPDATE'). 재실행 창에서 신규 행 0이 정상.
자기신고: scripts/dq_claim.py 의 record_claim 재사용 — 3분리
  source=API 가 준 데이터 행수, claimed=파서가 만든 유효 행수, persisted=테이블 행수 델타.
호출 정책: scripts/net_guard.py 경유(키 'naver' — naver_daily_backfill 과 프로세스 간 간격 공유),
  기본 간격 1.0s+0.2s 지터(스펙 '1초 이상'), 하루 2종목이라 부담 없음.

사용 (호스트 /usr/bin/python3):
  python3 scripts/index_backfill.py --from 2025-01-01 --to 2026-11-20
  python3 scripts/index_backfill.py --dry-run
"""
from __future__ import annotations

import argparse
import ast
import os
import sys
import time
import urllib.error
import urllib.request

import psycopg2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import dq_claim  # noqa: E402 — record_claim(자기신고 3분리 — 스펙 ④)
import net_guard  # noqa: E402 — 호출 정책(스펙: 네트워크는 net_guard 경유)

RUNNER = "index_backfill"
TABLE = "index_daily"
SOURCE = "naver_sise_day"
DEFAULT_CODES = ["KOSPI", "KOSDAQ"]

# 호출 정책(스펙: 연속 호출 간 1초 이상) — env 로 러너별 기존 이름을 그대로 존중.
NAVER_REQUEST_DELAY = float(os.environ.get("NAVER_REQUEST_DELAY", "1.0"))
NAVER_REQUEST_JITTER = float(os.environ.get("NAVER_REQUEST_JITTER", "0.2"))
NAVER_DAILY_BUDGET = int(os.environ.get("NAVER_DAILY_BUDGET", "0"))

DDL = """
CREATE TABLE IF NOT EXISTS index_daily (
    index_code TEXT      NOT NULL,
    trade_date DATE      NOT NULL,
    open       NUMERIC,
    high       NUMERIC,
    low        NUMERIC,
    close      NUMERIC,
    volume     BIGINT,
    PRIMARY KEY (index_code, trade_date)
)
"""

UPSERT_SQL = (
    "INSERT INTO index_daily (index_code, trade_date, open, high, low, close, volume)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s)"
    " ON CONFLICT (index_code, trade_date) DO UPDATE SET"
    " open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,"
    " close = EXCLUDED.close, volume = EXCLUDED.volume"
)


def connect():
    """DB 접속 — 좌표·자격증명은 프로세스 환경변수만(비밀번호 출력·파일기록 금지)."""
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"), connect_timeout=15)


def create_table(conn):
    cur = conn.cursor()
    try:
        cur.execute(DDL)
        conn.commit()
    finally:
        cur.close()


def table_count(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM index_daily")
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def parse_response(text):
    """Naver siseJson 일봉 응답 파싱 → (raw_n, rows, skipped).

    실측 형식(2026-11-20): 선두 '\n ' + 행간 탭 → ast.literal_eval(원문)은 IndentationError.
    .strip() 후 파싱한다(실측: strip 만으로 459행 정상). 헤더행(홑따옴표, 날짜=4글자)은 스킵.
    데이터행: [YYYYMMDD, 시가, 고가, 저가, 종가, 거래량, 외국인소진율] — 7번째 컬럼은 저장 안 함.
    OHLC 중 비수치(null·빈 값)가 하나라도 있으면 그 행은 건너뛰고 skipped 로 센다(스펙 테스트 ③).
    거래량은 비수치면 NULL 로 둔다(가격 경로는 있으므로 행을 버리지 않음 — 결측으로 버리면
    가격 정보까지 잃는다). rows 는 (date, open, high, low, close, volume) 오름차순.
    파싱 불가 응답(차단 페이지 등)은 (0, [], 0) — 실패가 아니라 빈 취급(부분 성공 허용).
    """
    try:
        data = ast.literal_eval(text.strip())
    except (ValueError, SyntaxError, TypeError):
        return 0, [], 0
    if not isinstance(data, list):
        return 0, [], 0
    raw_n, skipped, rows = 0, 0, []
    for r in data:
        if not (isinstance(r, list) and r and isinstance(r[0], str)
                and len(r[0]) == 8 and r[0].isdigit()):
            continue                              # 헤더행 등 스킵
        raw_n += 1
        vals = r[1:5]                             # OHLC 4값
        if len(vals) < 4 or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                                for v in vals):
            skipped += 1                          # 결측 행 — 건너뛰되 카운트에 기록(스펙 ③)
            continue
        vol = r[5] if len(r) > 5 else None
        if not isinstance(vol, (int, float)) or isinstance(vol, bool):
            vol = None                            # 거래량만 없으면 NULL(위 docstring 참조)
        rows.append(("%s-%s-%s" % (r[0][:4], r[0][4:6], r[0][6:8]),
                     float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3]),
                     None if vol is None else int(vol)))
    rows.sort()
    return raw_n, rows, skipped


def store_rows(conn, rows):
    """UPSERT(멱등) — rows: [(index_code, trade_date, open, high, low, close, volume)].

    ON CONFLICT DO UPDATE(스펙): 재실행 시 행수 불변 + 값은 최신으로. 반환 rowcount 는
    '영향받은 행'(신규+갱신) — 신규 행수(델타)는 호출부가 COUNT 전후로 계산한다.
    """
    cur = conn.cursor()
    try:
        cur.executemany(UPSERT_SQL, rows)
        n = int(cur.rowcount or 0)
        conn.commit()
    finally:
        cur.close()
    return n


def make_fetcher(opener, g, retry_sleep=1.0, timeout=60):
    """(code, start_yyyymmdd, end_yyyymmdd) → 응답 원문(str). 1회 재시도, 그래도 실패면 예외 위임.

    net_guard 획득(정책 중단 = GuardError, 재시도 금지) → 프록시 무시 opener → 응답 분류 기록.
    """
    def fetch(code, start_yyyymmdd, end_yyyymmdd):
        url = ("https://api.finance.naver.com/siseJson.naver?symbol=%s"
               "&requestType=1&startTime=%s&endTime=%s&timeframe=day"
               % (code, start_yyyymmdd, end_yyyymmdd))
        last = None
        for _ in range(2):
            try:
                g.acquire()
                try:
                    r = opener.open(url, timeout=timeout)
                except urllib.error.HTTPError as exc:
                    g.note(exc.code, exc.read()[:200])
                    raise
                body = r.read()
                g.note(r.getcode(), body[:200])
                try:
                    return body.decode("utf-8")   # 데이터 응답은 UTF-8(실측)
                except UnicodeDecodeError:
                    return body.decode("euc-kr")  # 빈 응답은 EUC-KR(분봉 실측과 동일 경로)
            except net_guard.GuardError:
                raise                              # 정책 중단(차단·예산) — 재시도 금지
            except Exception as exc:               # noqa: BLE001 — 1회 재시도 후 예외 위임
                last = exc
                time.sleep(retry_sleep)
        raise last
    return fetch


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="지수 일봉 백필 (docs/spec_index_backfill.md)")
    ap.add_argument("--from", dest="from_", default="2025-01-01")
    ap.add_argument("--to", default="2026-11-20")
    ap.add_argument("--codes", default=",".join(DEFAULT_CODES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.from_ >= args.to:
        ap.error("--from 은 --to 보다 앞이어야 합니다")
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        ap.error("--codes 에 종목이 없습니다")

    g = net_guard.guard("naver", delay=NAVER_REQUEST_DELAY,
                        jitter=NAVER_REQUEST_JITTER, budget=NAVER_DAILY_BUDGET)
    # 프록시 무시(ProxyHandler({})) — curl --noproxy '*' 와 동등. 네트워크는 Naver 시세 API 만.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.addheaders = [("User-Agent", "Mozilla/5.0 (analyist-dd index_backfill)")]
    fetch = make_fetcher(opener, g)

    s, e = args.from_.replace("-", ""), args.to.replace("-", "")
    stats = {"codes": len(codes), "rows_received": 0, "rows_parsed": 0,
             "rows_written": 0, "skipped": 0, "codes_empty": 0, "codes_failed": 0}

    conn = None if args.dry_run else connect()   # dry-run 은 DB 를 열지 않는다(조회·파싱만)
    try:
        if conn is not None:
            create_table(conn)                   # 신규 index_daily 만 — 기존 테이블 무변경
            before = table_count(conn)
        for code in codes:
            try:
                text = fetch(code, s, e)
            except net_guard.GuardError as exc:
                print("[backfill] %s: 수집 정책 중단(%s: %s) — 종료"
                      % (code, type(exc).__name__, exc))
                return 1
            except Exception as exc:             # noqa: BLE001 — 부분 성공 허용(스펙)
                stats["codes_failed"] += 1
                print("[backfill] %s %s~%s 실패(%s: %s) — 계속"
                      % (code, args.from_, args.to, type(exc).__name__, exc))
                continue
            raw_n, rows, skipped = parse_response(text)
            stats["rows_received"] += raw_n
            stats["rows_parsed"] += len(rows)
            stats["skipped"] += skipped
            written = 0
            if rows and conn is not None:
                written = store_rows(
                    conn, [(code, d, o, h, l, c, v) for d, o, h, l, c, v in rows])
            print("[backfill] %s %s~%s 원천 %d행 → 유효 %d행(결측 스킵 %d) · upsert %d행"
                  % (code, args.from_, args.to, raw_n, len(rows), skipped, written))
            if raw_n == 0:
                stats["codes_empty"] += 1
        if conn is not None:
            stats["rows_written"] = table_count(conn) - before
    finally:
        if conn is not None:
            conn.close()

    if args.dry_run:
        print("[dry-run] 저장 없음(조회·파싱만) — rows_parsed=%d" % stats["rows_parsed"])
    else:
        print("codes=%d, window=%s~%s, rows_received=%d, rows_parsed=%d,"
              " rows_written=%d, skipped=%d, codes_empty=%d, codes_failed=%d"
              % (stats["codes"], args.from_, args.to, stats["rows_received"],
                 stats["rows_parsed"], stats["rows_written"], stats["skipped"],
                 stats["codes_empty"], stats["codes_failed"]))
        _claim(stats, args)
    return 0


def _claim(stats, args):
    """자기신고(스펙 ④) — dq_claim.record_claim 재사용 + 마지막 줄 [claim] 출력.

    source=API 가 준 데이터 행수, claimed=파서가 만든 유효 행수, persisted=테이블 행수 델타.
    자기신고 커넥션은 **단명**(dq_claim 설계 원칙 — 장수명 트랜잭션이 DDL 과 교착한 실측 사고
    2026-09-28 재발 방지), 자기신고 실패가 수집을 깨면 안 된다 — stderr 경고 후에도 숫자 줄은 남긴다.
    """
    delta = stats["rows_written"]
    note = ("codes=%s window=%s~%s skipped=%d"
            % (args.codes, args.from_, args.to, stats["skipped"]))
    try:
        conn = connect()
        try:
            dq_claim.record_claim(conn, RUNNER, TABLE, claimed_rows=stats["rows_parsed"],
                                  persisted_rows=delta, source_rows=stats["rows_received"],
                                  note=note)
        finally:
            conn.close()
    except Exception as exc:                     # noqa: BLE001 — 수집을 깨지 않는다
        print("[claim] %s: 자기신고 실패(%s: %s)" % (RUNNER, type(exc).__name__, exc),
              file=sys.stderr)
    print("[claim] %s -> %s source=%d claimed=%d persisted=%d"
          % (RUNNER, TABLE, stats["rows_received"], stats["rows_parsed"], delta),
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
