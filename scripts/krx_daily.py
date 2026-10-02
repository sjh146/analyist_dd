#!/usr/bin/env python3
"""KRX OpenAPI 일별매매정보 수집기 — GPU 머신(BC250) 수리 전 KIS 대체 경로.

설계 원칙: **IP 차단 이력이 있으므로 절대 빠르게/반복적으로 때리지 않는다.**
  1) 호출 간 기본 3.0초 + 지터 0~0.5초 (env 로 조정)
  2) 실행/크론 간 최소 간격을 상태파일로 공유 (앞선 실행이 끝난 직후 몰아치기 방지)
  3) 401/403/429/5xx 또는 respCode != 200 → 재시도 없이 즉시 중단 (IP 보호)
  4) 실행당 호출 상한(기본 600) + 날짜별 진행파일로 재개
  5) 공식 OpenAPI(/svc/apis)만 사용 — 차단을 유발한 스크래핑 엔드포인트
     (comm/bldAttendant/getJsonData.cmd)는 절대 호출하지 않는다.

호출 구조: 날짜 1개 = 시장 2콜(유가증권 stk_bydd_trd + 코스닥 ksq_bydd_trd),
응답 OutBlock_1 에 그날 전 종목이 들어있다 → 1년치 = 약 262일 × 2 = 524콜(≈30분).

사용 (호스트):
  set -a; . .env; set +a; export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  python3 scripts/krx_daily.py --dry-run                 # 계획만 (네트워크 0회)
  python3 scripts/krx_daily.py --days 30                 # 최근 30일부터 백필
  python3 scripts/krx_daily.py --from 20250827 --to 20260918
  python3 scripts/krx_daily.py                            # DB 최신일+1 ~ 오늘(2영업일 전까지)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

import psycopg2

try:  # 자기신고(R23) — 배선 실패가 수집을 깨지 않도록 방어적으로 import
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dq_claim import claim_start, claim_finish  # noqa: E402
except Exception:  # noqa: BLE001
    claim_start = claim_finish = None

PROJ = os.environ.get("PROJ_DIR", "/home/dduckbeagy/analyist_dd")
HOLIDAY_PATH = os.path.join(PROJ, "data", "krx_holidays.json")
STATE_PATH = os.path.join(PROJ, "data", "krx", "rate_state.json")
PROGRESS_DIR = os.path.join(PROJ, "data", "krx")

# ── 보수적 호출 정책 (환경변수로만 조정) ─────────────────────────────
DELAY = float(os.environ.get("KRX_REQUEST_DELAY", "3.0"))
JITTER = float(os.environ.get("KRX_REQUEST_JITTER", "0.5"))
MIN_GAP_BETWEEN_RUNS = float(os.environ.get("KRX_MIN_GAP_BETWEEN_RUNS", "60.0"))
MAX_CALLS = int(os.environ.get("KRX_MAX_CALLS", "600"))
# 증분 시작일 계산용: 이 행수 미만이면 '덜 찬 날'로 보고 그 날부터 다시 받는다
MIN_ROWS_FULL_DAY = int(os.environ.get("KRX_MIN_ROWS_FULL_DAY", "2000"))
LOOKBACK_DAYS = int(os.environ.get("KRX_LOOKBACK_DAYS", "7"))

try:  # 호출 정책(프로세스 간) — 로드 실패해도 수집은 기존(프로세스 내) 지연으로 동작한다
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import net_guard  # noqa: E402
except Exception:  # noqa: BLE001
    net_guard = None

# 마켓 → OpenAPI 서비스 경로. 승인된 서비스만 호출된다.
MARKETS = {"KOSPI": "sto/stk_bydd_trd", "KOSDAQ": "sto/ksq_bydd_trd"}

# KRX OpenAPI 호스트 후보 (앞에서부터 시도, 404/HTML 이면 다음 후보)
BASE_CANDIDATES = [
    u.strip() for u in os.environ.get(
        "KRX_BASE_URLS",
        "https://data.krx.co.kr/svc/apis,https://data-dbg.krx.co.kr/svc/apis",
    ).split(",") if u.strip()
]

PG = dict(
    host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    port=int(os.environ.get("POSTGRES_PORT", "5434")),
    user=os.environ.get("POSTGRES_USER", "stock_user"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
    dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
)

UPSERT_MD = """
INSERT INTO market_data
  (stock_code, trade_date, open_price, high_price, low_price,
   close_price, volume, trading_value)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
  open_price=EXCLUDED.open_price, high_price=EXCLUDED.high_price,
  low_price=EXCLUDED.low_price, close_price=EXCLUDED.close_price,
  volume=EXCLUDED.volume, trading_value=EXCLUDED.trading_value
"""

UPSERT_STOCK = """
INSERT INTO stocks (stock_code, stock_name, market)
VALUES (%s,%s,%s)
ON CONFLICT (stock_code) DO UPDATE SET
  stock_name=EXCLUDED.stock_name, market=EXCLUDED.market,
  updated_at=CURRENT_TIMESTAMP
"""


class KrxBlocked(Exception):
    """차단/비승인 신호 — 재시도 금지, 즉시 종료."""


def num(v) -> float | None:
    """'1,234' / '' / '-' 형태의 KRX 문자열 → float."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s in {"-", "0"}:
        return 0.0 if s == "0" else None
    try:
        return float(s)
    except ValueError:
        return None


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def wait_for_run_gap():
    """직전 실행과 최소 간격 확보 (크론/수동 중복 실행 방어)."""
    st = load_json(STATE_PATH, {})
    last = float(st.get("last_run_end", 0) or 0)
    gap = time.time() - last
    if last and gap < MIN_GAP_BETWEEN_RUNS:
        wait = MIN_GAP_BETWEEN_RUNS - gap
        print(f"직전 실행 종료 후 {gap:.0f}초 — 최소 간격 확보를 위해 {wait:.0f}초 대기", flush=True)
        time.sleep(wait)
    st["last_run_start"] = time.time()
    save_json(STATE_PATH, st)


def mark_run_end():
    st = load_json(STATE_PATH, {})
    st["last_run_end"] = time.time()
    save_json(STATE_PATH, st)


def resolve_base(key):
    """후보 호스트 중 OpenAPI 로 응답하는 첫 번째를 고른다 (404/HTML 제외)."""
    cached = load_json(STATE_PATH, {}).get("base_url")
    if cached:
        return cached
    for base in BASE_CANDIDATES:
        url = f"{base}/{MARKETS['KOSPI']}?basDd=20260918"
        req = urllib.request.Request(url, headers={"AUTH_KEY": key})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8", "replace").strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace").strip()
            if e.code == 404 or body.startswith("<"):
                continue
        except Exception:  # noqa: BLE001 — 네트워크 오류는 다음 후보로
            continue
        if body.startswith("{"):
            st = load_json(STATE_PATH, {})
            st["base_url"] = base
            save_json(STATE_PATH, st)
            print(f"KRX base: {base}", flush=True)
            return base
    raise KrxBlocked("KRX OpenAPI 호스트를 찾지 못했습니다 (모두 404/HTML 응답)")


def _guard():
    """KRX 호스트 호출 정책(프로세스 간 간격·예산·쿨다운). 없으면 None.

    WHY: 이 러너의 지연은 자기 프로세스 안에서만 유효하다 — 수동 백필과 크론이 겹치면
    같은 IP 로 실제 호출률이 2배가 되고(차단 이력이 있는 호스트다) 로그는 양쪽 다 정상으로 보인다.
    """
    if net_guard is None:
        return None
    try:
        return net_guard.guard("krx", delay=DELAY, jitter=JITTER, budget=MAX_CALLS)
    except Exception:  # noqa: BLE001
        return None


def _guard_block(g, reason):
    """차단 신호를 호스트 키에 기록 — 다른 프로세스도 즉시 멈춘다."""
    if g is None:
        return
    try:
        g.block(reason)
    except Exception:  # noqa: BLE001
        pass


def fetch_day(base, key, market_path, basdd):
    """1일 1시장 호출. 차단/비승인 신호는 KrxBlocked 로 즉시 중단."""
    g = _guard()
    if g is not None:
        try:
            g.acquire()
        except Exception as e:  # noqa: BLE001 — 정책 중단(쿨다운·예산)도 즉시 종료로 처리
            raise KrxBlocked(f"수집 정책 중단({type(e).__name__}): {e}") from e
    url = f"{base}/{market_path}?basDd={basdd}"
    req = urllib.request.Request(url, headers={"AUTH_KEY": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
    except Exception as e:  # noqa: BLE001
        raise KrxBlocked(f"전송 오류({e}) — 재시도하지 않고 중단 (IP 보호)")

    if code in (401, 403, 429) or code >= 500:
        _guard_block(g, f"HTTP {code} {raw.strip()[:100]}")
        raise KrxBlocked(f"HTTP {code} {raw.strip()[:120]} — 재시도 없이 종료 (IP 보호)")
    try:
        data = json.loads(raw)
    except ValueError:
        _guard_block(g, f"비JSON 응답(HTTP {code}) {raw.strip()[:80]}")
        raise KrxBlocked(f"비JSON 응답(HTTP {code}) {raw.strip()[:120]}")
    # 정상 응답에는 respCode 가 아예 없다(OutBlock_1 만 온다) — 있으면 오류 코드만 검사한다.
    resp_code = str(data.get("respCode", "") or "")
    if resp_code and resp_code not in ("200", "0"):
        if resp_code in ("401", "403", "429") or resp_code.startswith("5"):
            _guard_block(g, f"respCode={resp_code} {data.get('respMsg')}")
        raise KrxBlocked(f"respCode={data.get('respCode')} {data.get('respMsg')}")
    rows = data.get("OutBlock_1")
    if rows is None:
        _guard_block(g, f"OutBlock_1 없음(HTTP {code}) {raw.strip()[:80]}")
        raise KrxBlocked(f"OutBlock_1 없음(HTTP {code}) {raw.strip()[:120]}")
    return rows


def trading_dates(start, end, holidays):
    """평일 − 휴장 파일. KRX 응답의 빈 시세일은 실행 중 추가 기록한다."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


def _incremental_start(conn, today):
    """증분 시작일 = **최근 창에서 커버리지가 모자란 거래일**, 없으면 DB 최신일+1.

    WHY (실측 2026-10-02): 시작일을 MAX(trade_date)+1 로 잡으면, 다른 작성자
    (yfinance 수집기·파이프라인 heredoc)가 **오늘 봉 1건만** 넣는 순간 max 가 오늘이 되고
    구간이 [오늘+1, 어제] = 공집합이 된다 → 로그에는 "수집 구간 없음"으로 조용히 남고
    **어제 봉은 영구 결손**된다(실측: 000020 1행 때문에 2026-10-02 전 종목 봉이 사라질 상태였다).
    행수 기준으로 '덜 찬 날'부터 다시 받으면 구멍이 다음 실행에서 스스로 메워진다(멱등 upsert).

    임계값 아래로 잡지 않기 위해 최근 LOOKBACK 일만 본다(휴장 파일은 제외).
    """
    holidays = set(load_json(HOLIDAY_PATH, []))
    cur = conn.cursor()
    since = today - timedelta(days=LOOKBACK_DAYS)
    cur.execute("SELECT trade_date::text, COUNT(*) FROM market_data WHERE trade_date >= %s "
                "GROUP BY 1", (since,))
    counts = {str(r[0]): int(r[1]) for r in cur.fetchall()}
    cur.execute("SELECT MAX(trade_date) FROM market_data")
    mx = cur.fetchone()[0]
    cur.close()
    d = since
    while d < today:
        key = d.isoformat()
        if d.weekday() < 5 and key not in holidays and counts.get(key, 0) < MIN_ROWS_FULL_DAY:
            return d
        d += timedelta(days=1)
    return (mx + timedelta(days=1)) if mx else (today - timedelta(days=LOOKBACK_DAYS))


def target_range(conn, args):
    cur = conn.cursor()
    cur.execute("SELECT MAX(trade_date) FROM market_data")
    mx = cur.fetchone()[0]
    cur.close()
    today = date.today()
    if args.d_from:
        start = date.fromisoformat(args.d_from) if "-" in args.d_from else datetime.strptime(args.d_from, "%Y%m%d").date()
    elif args.days:
        start = today - timedelta(days=args.days)
    else:
        start = _incremental_start(conn, today)
    end = (date.fromisoformat(args.d_to) if "-" in (args.d_to or "")
           else datetime.strptime(args.d_to, "%Y%m%d").date()) if args.d_to else today
    # KRX 일별매매정보는 당일 데이터를 주지 않는다 → 안전하게 종료일을 D-1로 (실제로는 D-2 반영)
    if end >= today:
        end = today - timedelta(days=1)
    return start, end


def main():
    ap = argparse.ArgumentParser(description="KRX OpenAPI 일별매매정보 수집 (KIS 대체)")
    ap.add_argument("--from", dest="d_from", default=None, help="YYYYMMDD 또는 YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", default=None)
    ap.add_argument("--days", type=int, default=0, help="오늘 기준 최근 N일")
    ap.add_argument("--markets", default="KOSPI,KOSDAQ")
    ap.add_argument("--limit", type=int, default=0, help="점검용 날짜 수 제한")
    ap.add_argument("--dry-run", action="store_true", help="네트워크 호출 없이 계획만 출력")
    ap.add_argument("--ignore-run-gap", action="store_true", help="실행 간 최소 간격 무시(수동 백필)")
    ap.add_argument("--ignore-progress", action="store_true",
                    help="진행파일 무시하고 해당 구간을 다시 조회 (공실/휴장 판별 프로브용 — "
                         "이미 처리한 날짜도 실제 응답을 보게 한다)")
    args = ap.parse_args()

    key = os.environ.get("KRX_API_KEY", "").strip()
    conn = psycopg2.connect(**PG)
    try:
        start, end = target_range(conn, args)
        if start > end:
            print(f"수집 구간 없음: {start} ~ {end} (DB 최신일 기준)", flush=True)
            return 0

        holidays = set(load_json(HOLIDAY_PATH, []))
        dates = trading_dates(start, end, holidays)
        markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()]
        unknown = [m for m in markets if m not in MARKETS]
        if unknown:
            print(f"지원하지 않는 마켓: {unknown} (가능: {list(MARKETS)})", flush=True)
            return 2
        if args.limit:
            dates = dates[:args.limit]

        planned = len(dates) * len(markets)
        est_min = planned * (DELAY + JITTER / 2) / 60
        print(f"구간 {start} ~ {end} / 영업일 {len(dates)}일 × {len(markets)}마켓 = {planned}콜 "
              f"(예상 {est_min:.1f}분, 상한 {MAX_CALLS}콜)", flush=True)

        if args.dry_run:
            print("[dry-run] 호출 없음. 첫 5일:", ", ".join(d.strftime("%Y%m%d") for d in dates[:5]), flush=True)
            return 0

        if not key:
            print("KRX_API_KEY 미설정 — .env 를 채우세요.", flush=True)
            return 2

        prog = os.path.join(PROGRESS_DIR, f"progress_{start:%Y%m%d}_{end:%Y%m%d}.txt")
        done = set()
        if os.path.exists(prog):
            with open(prog) as f:
                done = {l.strip() for l in f if l.strip()}
        if args.ignore_progress:
            done = set()   # 프로브: 진행파일 때문에 응답을 못 보는 상황 방지
        os.makedirs(PROGRESS_DIR, exist_ok=True)

        if not args.ignore_run_gap:
            wait_for_run_gap()
        base = resolve_base(key)

        calls = 0
        days_ok = days_holiday = 0
        rows_total = 0
        # 소스(API)가 준 '유효 코드' 행수. 적재가 일어난 날짜만 센다 — KRX 가 아직 발행하지
        # 않은 날짜는 이 러너가 휴장으로 분류하므로(day_rows 비어 있음) 소스 수신으로 세면
        # 거짓 parse_failure 가 된다.
        recv_total = 0
        if claim_start:
            claim_start("krx_daily", "market_data", note=f"{start}~{end}")
        try:
            for d in dates:
                basdd = d.strftime("%Y%m%d")
                if basdd in done:
                    continue
                if calls >= MAX_CALLS:
                    print(f"호출 상한({MAX_CALLS}) 도달 — 중단, 다음 실행에서 이어서", flush=True)
                    break
                day_rows, day_empty = [], 0
                day_recv = 0
                for m in markets:
                    batches = fetch_day(base, key, MARKETS[m], basdd)
                    calls += 1
                    for r in batches:
                        code = str(r.get("ISU_CD") or "").strip()
                        close = num(r.get("TDD_CLSPRC"))
                        if not code or not code.isdigit():
                            continue
                        day_recv += 1
                        if close is None:          # 휴장/거래정지 행
                            day_empty += 1
                            continue
                        day_rows.append((
                            code, d, num(r.get("TDD_OPNPRC")), num(r.get("TDD_HGPRC")),
                            num(r.get("TDD_LWPRC")), close, num(r.get("ACC_TRDVOL")),
                            num(r.get("ACC_TRDVAL")),
                        ))
                        # 종목 마스터 동기화 (ISU_NM/MKT_NM) — 시장/종목명 자동 갱신
                        if r.get("ISU_NM") and r.get("MKT_NM") in MARKETS:
                            cur = conn.cursor()
                            cur.execute(UPSERT_STOCK, (code, r["ISU_NM"].strip(), r["MKT_NM"]))
                            cur.close()
                    if DELAY or JITTER:
                        time.sleep(DELAY + random.uniform(0, JITTER))
                    # 첫 시장에서 전 종목이 전부 무가격이면 휴장으로 판단
                    if not day_rows and batches is not None and day_empty > 0:
                        pass
                if day_rows:
                    recv_total += day_recv
                    cur = conn.cursor()
                    cur.executemany(UPSERT_MD, day_rows)
                    conn.commit()
                    cur.close()
                    rows_total += len(day_rows)
                    days_ok += 1
                    with open(prog, "a") as f:
                        f.write(basdd + "\n")
                    print(f"{basdd}: {len(day_rows)}종목 적재 (누적 {days_ok}일)", flush=True)
                else:
                    # 시세가 전부 비어 있음 = 휴장(임시공휴일 포함) → 휴장 파일에 기록
                    holidays.add(d.isoformat())
                    save_json(HOLIDAY_PATH, sorted(holidays))
                    days_holiday += 1
                    with open(prog, "a") as f:
                        f.write(basdd + "\n")
                    print(f"{basdd}: 시세 없음 → 휴장 기록", flush=True)
        except KrxBlocked as e:
            print(f"중단: {e}", flush=True)
            mark_run_end()
            return 3
        mark_run_end()

        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT trade_date), COUNT(*), MIN(trade_date), MAX(trade_date) "
                    "FROM market_data WHERE trade_date BETWEEN %s AND %s", (start, end))
        dd, n, mn, mx = cur.fetchone()
        cur.close()
        print(f"완료: {days_ok}영업일 적재, 휴장 {days_holiday}일, {rows_total}행 upsert, 호출 {calls}회", flush=True)
        print(f"DB(구간): {dd}일 {n}행 ({mn} ~ {mx})", flush=True)
        if claim_finish and calls:
            # 자기신고(R23): source=API 유효행, claimed=파서가 만든 행(가격 있는 행),
            # persisted=market_data 델타(헬퍼가 계산). 재실행 창의 gap 은 정상이다.
            claim_finish("krx_daily", source_rows=recv_total, claimed_rows=rows_total,
                         note=f"calls={calls} days_ok={days_ok} holiday={days_holiday}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    random.seed()
    sys.exit(main())
