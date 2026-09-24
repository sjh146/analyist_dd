#!/usr/bin/env python3
"""Macro indicator backfill -> macro_indicators (analyist_dd).

Revives the macro feature block (services/xgboost-ml/app/feature_engine/macro_features.py)
by actually persisting macro time series into `macro_indicators` — the table had no writer
anywhere in the repo (services/yfinance-collector/app/collectors/macro_collector.py only
returns a list and is never called from the service loop).

Indicator names written here are exactly the display names the feature reader expects:
    '기준금리', '국고채3년', '회사채3년', 'USD/KRW 환율', 'JPY/KRW 환율',
    'CNY/KRW 환율', 'WTI 유가', 'CPI', 'PPI'

Sources
-------
Primary (key-free, verified reachable): FRED CSV export
    https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>&cosd=<start>
  DEXKOUS   Korea / U.S. Foreign Exchange Rate (KRW per USD)      -> 'USD/KRW 환율'
  DEXJPUS   Japan / U.S. Foreign Exchange Rate (JPY per USD)      -> 'JPY/KRW 환율' (derived)
  DEXCHUS   China / U.S. Foreign Exchange Rate (CNY per USD)      -> 'CNY/KRW 환율' (derived)
  DCOILWTICO  WTI crude spot (Cushing, USD/bbl)                   -> 'WTI 유가'
  Derived series are inner-joined on observation_date with DEXKOUS, so every written row
  is a real observation (no interpolation, no back-fill of missing days).

ECOS (Bank of Korea) — KEY REQUIRED, currently BLOCKED.
  As of the last run ECOS_API_KEY in .env is empty/invalid and the API answers
    {"RESULT":{"CODE":"INFO-100","MESSAGE":"인증키가 유효하지 않습니다..."}}
  So rate/bond/CPI/PPI cannot be sourced from ECOS right now. The ECOS code path below is
  kept intact and is used automatically as soon as a valid ECOS_API_KEY is exported
  (env var only — this script never reads or writes .env).
  Once the key works:  ECOS_API_KEY=<key> python3 scripts/macro_backfill.py
  (optionally --only 기준금리,국고채3년,회사채3년,CPI,PPI)

Idempotent: every row is upserted on UNIQUE(indicator_name, date) — re-running is safe.

Usage
-----
  python3 scripts/macro_backfill.py                      # 3 years, all available sources
  python3 scripts/macro_backfill.py --years 5
  python3 scripts/macro_backfill.py --only "USD/KRW 환율","WTI 유가"
  python3 scripts/macro_backfill.py --dry-run            # fetch+parse, no DB write
  python3 scripts/macro_backfill.py --verify             # print stored coverage only

Env (never printed):
  POSTGRES_HOST/POSTGRES_PORT/POSTGRES_DB/POSTGRES_USER/POSTGRES_PASSWORD
  (fallbacks: MACRO_DB_HOST/PORT/NAME/USER/PASSWORD; host default 127.0.0.1:5434)
  ECOS_API_KEY  (optional; ECOS path skipped when absent)
  FRED_BASE_URL (optional override; default https://fred.stlouisfed.org)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

UA = "analyist-dd-macro-backfill/1.0 (+https://fred.stlouisfed.org)"
FRED_BASE_URL = os.environ.get("FRED_BASE_URL", "https://fred.stlouisfed.org").rstrip("/")
ECOS_BASE_URL = os.environ.get("ECOS_BASE_URL", "https://ecos.bok.or.kr/api")
HTTP_TIMEOUT = int(os.environ.get("MACRO_HTTP_TIMEOUT", "30"))
HTTP_RETRIES = int(os.environ.get("MACRO_HTTP_RETRIES", "3"))

UNIT = {
    "기준금리": "percent",
    "국고채3년": "percent",
    "회사채3년": "percent",
    "USD/KRW 환율": "KRW",
    "JPY/KRW 환율": "KRW",
    "CNY/KRW 환율": "KRW",
    "WTI 유가": "USD/barrel",
    "CPI": "index",
    "PPI": "index",
}

# FRED series -> indicator name (1:1 sources)
FRED_DIRECT: Dict[str, str] = {
    "DEXKOUS": "USD/KRW 환율",
    "DCOILWTICO": "WTI 유가",
}
# FRED series used to derive cross rates against DEXKOUS
FRED_CROSS: Dict[str, Tuple[str, float]] = {
    # series_id: (indicator name, multiplier applied to (DEXKOUS / series))
    "DEXJPUS": ("JPY/KRW 환율", 100.0),  # KRW per 100 JPY (ECOS 731Y001/0000002 convention)
    "DEXCHUS": ("CNY/KRW 환율", 1.0),  # KRW per 1 CNY   (ECOS 731Y001/0000005 convention)
}
FRED_BASE_FX_SERIES = "DEXKOUS"

# ECOS (Bank of Korea) codes — 실측 확인(2026-09-24, 실제 응답으로 검증).
#   주의: 시장금리(일별)는 817Y002 이고 국고채/회사채가 **같은 표의 다른 ITEM** 이다.
#   종전 값(721Y001/0102000 등)은 존재하지 않는 조합 → "INFO-200 데이터 없음" 으로
#   조용히 실패했다. PPI 는 404Y014 의 총지수(*AA)다.
ECOS_INDICATORS: Dict[str, Tuple[str, str, str]] = {
    "기준금리": ("722Y001", "D", "0101000"),
    "국고채3년": ("817Y002", "D", "010200000"),
    "회사채3년": ("817Y002", "D", "010300000"),
    "CPI": ("901Y009", "M", "0"),
    "PPI": ("404Y014", "M", "*AA"),
}

LOG_PREFIX = "[macro_backfill]"


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


# --------------------------------------------------------------------------- http


def http_get(url: str, timeout: int = HTTP_TIMEOUT) -> bytes:
    last_err: Optional[Exception] = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:  # 4xx/5xx carry a status we want in the report
            last_err = exc
            if exc.code in (400, 401, 403, 404, 410, 429) or attempt == HTTP_RETRIES:
                raise
        except Exception as exc:  # noqa: BLE001 - network flake, retry
            last_err = exc
        time.sleep(1.5 * attempt)
    raise RuntimeError(f"GET failed: {url} ({last_err!r})")


# --------------------------------------------------------------------------- FRED


def fetch_fred_series(series_id: str, start: date) -> Dict[date, float]:
    """Download one FRED series as {observation_date: value}. Empty dict when unavailable."""
    qs = urllib.parse.urlencode({"id": series_id, "cosd": start.isoformat()})
    url = f"{FRED_BASE_URL}/graph/fredgraph.csv?{qs}"
    raw = http_get(url).decode("utf-8", "replace")
    if raw.lstrip().startswith("<"):  # HTML error page / bot wall
        raise RuntimeError(f"FRED returned HTML for {series_id}")
    reader = csv.reader(io.StringIO(raw))
    header = next(reader, None)
    if not header or series_id not in header[1]:
        raise RuntimeError(f"unexpected FRED header for {series_id}: {header}")
    out: Dict[date, float] = {}
    for row in reader:
        if len(row) < 2:
            continue
        d, v = row[0].strip(), row[1].strip()
        if not v or v == "." or v == "":  # FRED uses '.' for holidays
            continue
        try:
            out[date.fromisoformat(d)] = float(v)
        except ValueError:
            continue
    return out


def build_fred_series(start: date, wanted: Optional[Iterable[str]]) -> Tuple[Dict[str, Dict[date, float]], List[str]]:
    """Return ({indicator_name: {date: value}}, [failure descriptions])."""
    results: Dict[str, Dict[date, float]] = {}
    failures: List[str] = []

    def want(name: str) -> bool:
        return wanted is None or name in wanted

    fetched: Dict[str, Dict[date, float]] = {}
    needed = set([FRED_BASE_FX_SERIES]) if any(want(n) for n, _ in FRED_CROSS.values()) else set()
    needed |= {sid for sid, name in FRED_DIRECT.items() if want(name)}
    needed |= {sid for sid, (name, _) in FRED_CROSS.items() if want(name)}

    for sid in sorted(needed):
        try:
            fetched[sid] = fetch_fred_series(sid, start)
            log(f"FRED {sid}: {len(fetched[sid])} obs "
                f"({min(fetched[sid]) if fetched[sid] else '-'} .. {max(fetched[sid]) if fetched[sid] else '-'})")
        except Exception as exc:  # noqa: BLE001
            fetched[sid] = {}
            failures.append(f"FRED {sid}: {type(exc).__name__}: {exc}")

    for sid, name in FRED_DIRECT.items():
        if want(name):
            if not fetched.get(sid):
                if not any(f"FRED {sid}:" in f for f in failures):
                    failures.append(f"FRED {sid}: empty series for {name}")
                continue
            results[name] = dict(fetched[sid])

    base = fetched.get(FRED_BASE_FX_SERIES, {})
    for sid, (name, mult) in FRED_CROSS.items():
        if not want(name):
            continue
        src = fetched.get(sid, {})
        if not base or not src:
            failures.append(f"FRED {sid}: cannot derive {name} (base/quote series empty)")
            continue
        derived = {d: round(base[d] / src[d] * mult, 4) for d in base.keys() & src.keys() if src[d]}
        if derived:
            results[name] = derived
        else:
            failures.append(f"FRED {sid}: no overlapping dates with {FRED_BASE_FX_SERIES} for {name}")

    return results, failures


# --------------------------------------------------------------------------- ECOS


def ecos_api_key() -> str:
    """Read the key from the environment only. Never touches or prints .env."""
    return (os.environ.get("ECOS_API_KEY") or "").strip()


def fetch_ecos_series(start: date, end: date, api_key: str, stat_code: str, cycle: str, item: str):
    """One ECOS StatisticSearch call. Raises RuntimeError with the ECOS RESULT code on failure."""
    fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
    # ECOS 경로에는 `StatisticSearch` 세그먼트가 **반드시** 있어야 한다. 빠지면
    # 게이트웨이가 HTTP 404(빈 본문)를 돌려주고, 코드는 그걸 "키 무효"로 오인한다
    # (실측 2026-09-24: 정상 키로도 5개 시리즈 전부 HTTP 404 → 세그먼트 추가로 해결).
    url = (f"{ECOS_BASE_URL}/StatisticSearch/{urllib.parse.quote(api_key)}/json/kr/1/1000/"
           f"{stat_code}/{cycle}/{start.strftime(fmt)}/{end.strftime(fmt)}/{item}")
    payload = json.loads(http_get(url).decode("utf-8", "replace"))
    if "StatisticSearch" not in payload:
        res = payload.get("RESULT", {})
        raise RuntimeError(f"ECOS {res.get('CODE', '?')}: {res.get('MESSAGE', payload)}")
    rows = payload["StatisticSearch"].get("row", [])
    out: Dict[date, float] = {}
    for row in rows:
        t, v = (row.get("TIME") or "").strip(), (row.get("DATA_VALUE") or "").strip()
        if not t or not v:
            continue
        try:
            value = float(v.replace(",", ""))
            d = (datetime.strptime(t, "%Y%m%d").date() if cycle == "D"
                 else datetime.strptime(t, "%Y%m").date().replace(day=1))
        except ValueError:
            continue
        out[d] = value
    return out


def build_ecos_series(start: date, end: date, wanted: Optional[Iterable[str]]):
    """Return ({indicator_name: {date: value}}, [failure descriptions])."""
    results: Dict[str, Dict[date, float]] = {}
    failures: List[str] = []
    key = ecos_api_key()
    if not key or key.lower().startswith("your_"):
        failures.append("ECOS: ECOS_API_KEY not set in environment (blocked; key must be reissued)")
        return results, failures
    for name, (stat_code, cycle, item) in ECOS_INDICATORS.items():
        if wanted is not None and name not in wanted:
            continue
        try:
            # 한 번에 1000행까지만 온다(요청 상한). 일별 시리즈는 3년치가 1000영업일을
            # 넘어 **최근 구간이 잘린다**(실측: 2023-09~2026-06 에서 끊김 — 가장 중요한
            # 최신 값이 사라진다). 연 단위로 나눠 호출해 병합한다.
            series: Dict[date, float] = {}
            if cycle == "D":
                chunk_start = date(start.year, 1, 1)
                while chunk_start <= end:
                    chunk_end = min(date(chunk_start.year, 12, 31), end)
                    series.update(fetch_ecos_series(chunk_start, chunk_end, key, stat_code, cycle, item))
                    chunk_start = date(chunk_start.year + 1, 1, 1)
            else:
                series = fetch_ecos_series(start, end, key, stat_code, cycle, item)
            if series:
                results[name] = series
                log(f"ECOS {name}: {len(series)} obs "
                    f"({min(series):%Y-%m-%d} .. {max(series):%Y-%m-%d})")
            else:
                failures.append(f"ECOS {name}: empty StatisticSearch rows")
        except urllib.error.HTTPError as exc:
            failures.append(f"ECOS {name}: HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"ECOS {name}: {type(exc).__name__}: {exc}")
    return results, failures


# --------------------------------------------------------------------------- DB


def db_config() -> Dict[str, Any]:
    host = os.environ.get("POSTGRES_HOST") or os.environ.get("MACRO_DB_HOST") or "127.0.0.1"
    port = os.environ.get("POSTGRES_PORT") or os.environ.get("MACRO_DB_PORT") or "5434"
    if host in ("postgres", "db"):
        port = "5432"
    return {
        "host": host,
        "port": int(port),
        "dbname": os.environ.get("POSTGRES_DB") or os.environ.get("MACRO_DB_NAME") or "stock_trading",
        "user": os.environ.get("POSTGRES_USER") or os.environ.get("MACRO_DB_USER") or "stock_user",
        "password": os.environ.get("POSTGRES_PASSWORD") or os.environ.get("MACRO_DB_PASSWORD") or "",
    }


def connect(cfg: Dict[str, object]):
    import psycopg2  # imported late so --dry-run works without the driver
    return psycopg2.connect(**cfg, connect_timeout=15)


UPSERT_SQL = """
INSERT INTO macro_indicators (indicator_name, date, value, unit)
VALUES (%s, %s, %s, %s)
ON CONFLICT (indicator_name, date)
DO UPDATE SET value = EXCLUDED.value, unit = EXCLUDED.unit
"""


def upsert(conn, series_by_name: Dict[str, Dict[date, float]]) -> int:
    written = 0
    with conn.cursor() as cur:
        for name, series in sorted(series_by_name.items()):
            unit = UNIT.get(name, "")
            rows = [(name, d, v, unit) for d, v in sorted(series.items())]
            for i in range(0, len(rows), 500):
                cur.executemany(UPSERT_SQL, rows[i:i + 500])
                written += len(rows[i:i + 500])
    conn.commit()
    return written


def report_db(conn) -> List[Tuple[str, int, Optional[date], Optional[date]]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT indicator_name, count(*), min(date), max(date)
            FROM macro_indicators GROUP BY indicator_name ORDER BY indicator_name
        """)
        return cur.fetchall()


# --------------------------------------------------------------------------- main


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill macro_indicators from key-free sources (FRED) + ECOS when a key exists")
    ap.add_argument("--years", type=int, default=3, help="how many years back to fetch (default 3)")
    ap.add_argument("--start", help="explicit start date YYYY-MM-DD (overrides --years)")
    ap.add_argument("--end", help="explicit end date YYYY-MM-DD (default today)")
    ap.add_argument("--only", help="comma-separated indicator names to limit the run")
    ap.add_argument("--source", choices=["all", "fred", "ecos"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="fetch and parse but do not write")
    ap.add_argument("--verify", action="store_true", help="only print stored coverage")
    args = ap.parse_args(argv)

    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=365 * args.years + 5)
    wanted = {s.strip() for s in args.only.split(",")} if args.only else None

    cfg = db_config()
    log(f"window {start} .. {end} | db {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['dbname']}")

    if args.verify:
        with connect(cfg) as conn:
            rows = report_db(conn)
            log(f"stored coverage: {sum(r[1] for r in rows)} rows / {len(rows)} indicator names")
            for name, cnt, lo, hi in rows:
                log(f"  {name:14s} {cnt:5d} rows  {lo} .. {hi}")
        return 0

    series: Dict[str, Dict[date, float]] = {}
    failures: List[str] = []
    if args.source in ("all", "fred"):
        s, f = build_fred_series(start, wanted)
        series.update(s)
        failures += f
    if args.source in ("all", "ecos"):
        s, f = build_ecos_series(start, end, wanted)
        series.update(s)
        failures += f

    log("--- fetch summary ---")
    for name in sorted(series):
        d = series[name]
        log(f"  {name:14s} {len(d):5d} obs  {min(d)} .. {max(d)}")
    if failures:
        log("--- sources unavailable ---")
        for f in failures:
            log(f"  {f}")

    if args.dry_run:
        log("dry-run: nothing written")
        return 0

    if not series:
        log("no series fetched; nothing to write")
        return 2

    with connect(cfg) as conn:
        written = upsert(conn, series)
        log(f"upserted {written} rows (idempotent on UNIQUE(indicator_name, date))")
        rows = report_db(conn)
        log(f"table now: {sum(r[1] for r in rows)} rows / {len(rows)} indicator names")
        for name, cnt, lo, hi in rows:
            log(f"  {name:14s} {cnt:5d} rows  {lo} .. {hi}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
