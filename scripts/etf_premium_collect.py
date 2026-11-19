#!/usr/bin/env python3
"""레버리지/인버스 ETF 괴리율(시장가 vs NAV) 스냅샷 수집기 — docs/spec_etf_premium_monitor.md §1.

WHY (2026-11-18 실측): 레버리지·인버스 ETF 의 **시장가/NAV 괴리**는 평균회귀·왜곡을 판정하는
원자재 데이터인데 이 리포에는 존재하지 않는다(DB 조회로 확인 — 아래). 이 스크립트는 장중 1분
스냅샷을 쌓아 ① 괴리 크기·지속시간 분포 ② 평균회귀 반감기 ③ "괴리 발생 후 N분 수익률" 기대값을
실측하기 위한 입력을 만든다(스펙: 매매가 아니라 계측부터). 주문·브리지 무접근.

실측 조회 명령(2026-11-18 실행, 접속은 이 프로세스 환경변수 — 비밀번호 출력 금지):
  -- curl -s --noproxy '*' 'https://finance.naver.com/api/sise/etfItemList.nhn' \
  --   -w 'HTTP %{http_code} size=%{size_download} type=%{content_type}' → HTTP 200 265,885B
  --   text/plain;charset=EUC-KR → bytes.decode('euc-kr') 후 json.loads 필수(스펙 실측 재확인)
  -- python3 -c "json.loads(open('/tmp/etf_probe.bin','rb').read().decode('euc-kr')) ..." →
  --   전 ETF 1,190종목, 그 중 itemname 에 '레버리지'|'인버스' 포함 102종목(≥100 — 스펙 검증 기준)
  --   필드 결측: itemcode/itemname/nowVal/nav/quant/amonut/marketSum 각각 0건
  --   (quant 는 당일 누적 거래량 — 분석 스크립트의 'volume_last_cum' 해석 근거)
  -- date +%Z → KST (호스트 로컬, 창 판정 기준)
  -- SELECT MAX(trade_date) FROM market_data → 2026-11-18 (당일이 거래일 — 휴장 아님)
  -- env | grep -iE '^(http|https|all|no)_proxy' → 없음 (requests 프록시 문제 없음)
  -- 휴장일: data/krx_holidays.json (["2025-08-15", ..., "2026-12-31"] 형태)

동작 계약(스펙 §1):
  - 1회 실행 = 스냅샷 1개. --interval 주면 --window 안에서 반복(기본 --once, 크론이 매분 호출).
  - 시장 시간 밖(주말·휴장일·창 밖)이면 아무 것도 하지 않고 exit 0(자기신고에 reason 남김).
  - 저장 2경로, 둘 다 멱등:
    · DB etf_premium_snapshots(없으면 CREATE TABLE IF NOT EXISTS) + ON CONFLICT (ts, code) DO NOTHING
    · data/etf_premium/YYYY-MM-DD.jsonl (한 줄 = 한 스냅샷; 같은 ts 줄 중복 스킵 + flock)
  - --min-premium-log: 콘솔에는 |괴리|≥0.3% 종목만 요약(저장은 전량).
  - 실패 fail-open: 오류 시 exit 0 + error 필드, 3회 연속 실패만 exit 1(알림 대상).
  - 호출 정책: scripts/net_guard.py 통과(키 naver_etflist — 스로틀 불필요 실측이라 지연 0,
    일일 예산 없음; 차단 쿨다운·프로세스 간 직렬화·감사 이벤트는 가드가 제공).

멱등·자기신고: dq_claim 의 claim_start/claim_finish(record_claim) 재사용 — 3분리
  source_rows=API 준 전체 행수, claimed_rows=레버리지/인버스 필터 후 행수, persisted=테이블 신규 행수.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
SCRIPT_DIR = str(REPO / "scripts")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import psycopg2  # noqa: E402

try:
    import net_guard  # noqa: E402
except Exception:  # noqa: BLE001 — 가드 없이도 수집은 동작(정책 약화는 self-report 에 표기)
    net_guard = None
try:
    import dq_claim  # noqa: E402
except Exception:  # noqa: BLE001 — 자기신고 없이도 수집은 동작
    dq_claim = None

TABLE = "etf_premium_snapshots"
SOURCE = "naver_etflist"
URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
GUARD_KEY = "naver_etflist"   # naver_daily_backfill 의 "naver" 키와 분리 — 이 API 는 스로틀
                              # 불필요(스펙 실측)라 지연 0. 키 분리 근거: 같은 키면 가드가
                              # '더 보수적인 쪽'으로 수렴해 0.5s 지연을 물려받는다.
RUNNER = "etf_premium_collect"
JSONL_DIR = REPO / "data" / "etf_premium"
HOLIDAYS_PATH = REPO / "data" / "krx_holidays.json"
STATE_PATH = REPO / "data" / "state" / "etf_premium_collect_state.json"
MAX_FAILS = 3                 # 연속 실패 임계(스펙 §1: 3회 연속 실패면 exit 1)

def _conn_target():
    """DB 좌표. dq_claim._conn_target 과 동일 규칙(실측 2026-11-18).

    WHY(실측): 저장소 .env 를 소싱하면 컨테이너 좌표(POSTGRES_HOST=postgres)로 덮여 호스트
    프로세스에서 이름해석 실패가 난다 — 실제 실행에서
    `OperationalError: could not translate host name "postgres"` 를 확인했다.
    스펙 §3 크론 줄이 .env 소싱 후 POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 를 덮어쓰는
    이유가 이것이다. 여기서는 dq_claim 처럼 코드로 되돌려 크론 줄 의존을 없앤다.
    """
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1") or "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434") or 5434)
    if host in ("postgres", "db") and not os.path.exists("/.dockerenv"):
        host = "127.0.0.1"
        port = int(os.environ.get("POSTGRES_HOST_PORT", "5434") or 5434)
    return host, port


def _pg_kwargs():
    host, port = _conn_target()
    return dict(host=host, port=port,
                dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
                user=os.environ.get("POSTGRES_USER", "stock_user"),
                password=os.environ.get("POSTGRES_PASSWORD", ""),
                connect_timeout=15)

# 스펙 §1 스키마 그대로 — ts·trade_date·code NOT NULL(PK 구성), 나머지는 결측 시 NULL(0 채움 금지)
DDL = """
CREATE TABLE IF NOT EXISTS etf_premium_snapshots (
    ts          TIMESTAMPTZ NOT NULL,
    trade_date  DATE        NOT NULL,
    code        TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    price       NUMERIC,
    nav         NUMERIC,
    premium_pct NUMERIC,
    volume      BIGINT,
    amount      BIGINT,
    source      TEXT        DEFAULT 'naver_etflist',
    PRIMARY KEY (ts, code)
)
"""

UPSERT_SQL = (
    "INSERT INTO etf_premium_snapshots"
    " (ts, trade_date, code, name, price, nav, premium_pct, volume, amount, source)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    " ON CONFLICT (ts, code) DO NOTHING"
)


def connect():
    return psycopg2.connect(**_pg_kwargs())


def load_holidays() -> set:
    """휴장일 집합(data/krx_holidays.json). 파일 없음/손상 시 빈 집합(fail-open)."""
    try:
        with open(HOLIDAYS_PATH, encoding="utf-8") as f:
            return {d for d in json.load(f)}
    except (OSError, ValueError):
        return set()


def in_window(now: datetime, window: str, holidays: set) -> bool:
    """지금이 수집 창(기본 09:00-15:30) 안의 거래일인가. 밖이면 아무 것도 하지 않는다(스펙 §1)."""
    if now.weekday() >= 5:
        return False
    if now.strftime("%Y-%m-%d") in holidays:
        return False
    start, _, end = window.partition("-")
    return start <= now.strftime("%H:%M") <= end


def premium_pct(now_val, nav):
    """괴리율 = (nowVal/nav − 1)×100, 소수 4자리. 값이 없거나 nav≤0 이면 None(0 채움 금지)."""
    try:
        p, n = float(now_val), float(nav)
    except (TypeError, ValueError):
        return None
    if p <= 0 or n <= 0:
        return None
    return round((p / n - 1.0) * 100.0, 4)


def filter_lev_inv(items):
    """유니버스 판정: itemname 에 '레버리지' 또는 '인버스' 포함(스펙 — stocks 테이블 무관)."""
    return [it for it in items
            if "레버리지" in it.get("itemname", "") or "인버스" in it.get("itemname", "")]


def parse_response(raw: bytes):
    """EUC-KR 본문 → (전체 종목수, 레버리지/인버스 행 dict 리스트).

    UTF-8 로 읽으면 종목명 깨짐/UnicodeDecodeError(스펙 실측) → bytes.decode("euc-kr").
    실측 2026-11-18: nowVal/nav 결측 0건이나 nav=0 가능 → premium_pct 는 None 으로 둔다.
    """
    try:
        data = json.loads(raw.decode("euc-kr"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"응답 파싱 실패: {e!r}") from e
    if not isinstance(data, dict) or data.get("resultCode") != "success":
        got = data.get("resultCode") if isinstance(data, dict) else type(data).__name__
        raise ValueError(f"resultCode != success: {got}")
    items = data.get("result", {}).get("etfItemList") or []
    rows = []
    for it in filter_lev_inv(items):
        rows.append({
            "code": str(it["itemcode"]),
            "name": it["itemname"],
            "price": float(it["nowVal"]) if it.get("nowVal") is not None else None,
            "nav": float(it["nav"]) if it.get("nav") is not None else None,
            "premium_pct": premium_pct(it.get("nowVal"), it.get("nav")),
            "volume": int(it["quant"]) if it.get("quant") is not None else None,
            "amount": int(it["amonut"]) if it.get("amonut") is not None else None,
        })
    return len(items), rows


def _guard():
    if net_guard is None:
        return None
    try:
        return net_guard.guard(GUARD_KEY, delay=0.0, jitter=0.0, budget=0)
    except Exception:  # noqa: BLE001
        return None


def fetch(g) -> bytes:
    """ETF 목록 1콜. 가드 예산/쿨다운 위반(BudgetExhausted·Blocked)은 호출부가 fail-open 처리."""
    if g is not None:
        g.acquire()
    r = requests.get(URL, timeout=20,
                     headers={"User-Agent": "Mozilla/5.0 (research; analyist_dd)"})
    if g is not None:
        g.note(r.status_code, r.text, headers=r.headers)
    r.raise_for_status()
    return r.content


def store_rows(conn, rows) -> int:
    """UPSERT(ON CONFLICT DO NOTHING) — 반환은 실제 신규 행수(멱등 계약, 테스트 ④)."""
    if not rows:
        return 0
    cur = conn.cursor()
    try:
        cur.executemany(UPSERT_SQL, rows)
        conn.commit()
        return cur.rowcount
    finally:
        cur.close()


def to_db_rows(ts, trade_date, rows):
    return [(ts, trade_date, r["code"], r["name"], r["price"], r["nav"],
             r["premium_pct"], r["volume"], r["amount"], SOURCE) for r in rows]


def append_jsonl(path: Path, line: dict) -> bool:
    """스냅샷 1줄 추가. 멱등: 같은 ts 줄이 이미 마지막 줄이면 스킵. flock 으로 다중 프로세스 안전.

    ⚠ 2026-11-19 실측 버그: 텍스트 모드(a+)에서 `tell()-4096` **바이트 오프셋**으로 seek 한 뒤
    read 하면 멀티바이트 문자가 중간에서 잘려 `UnicodeDecodeError: 0xbc` 로 **수집이 10:04 에
    멈췄다**(그 뒤 매분 크론이 같은 지점에서 죽음). 그래서 꼬리 읽기는 **바이너리 + errors=replace**
    로 한다 — 잘린 조각은 어차피 파싱 실패로 무시된다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if end > 0:
                f.seek(max(0, end - 4096))
                tail = f.read().decode("utf-8", errors="replace")
                lines = tail.splitlines()
                if lines:
                    try:
                        if json.loads(lines[-1]).get("ts") == line["ts"]:
                            return False        # 같은 ts 재실행 → 중복 줄 없음(멱등)
                    except ValueError:
                        pass
            f.write((json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
            return True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ── 연속 실패 상태(3회 연속 → exit 1, 스펙 §1) ─────────────────────────────
def _read_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(STATE_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


def bump_failure(err: str) -> int:
    """연속 실패 +1 후 현재 횟수 반환. 성공 시 reset_failure 로 초기화된다."""
    st = _read_state()
    n = int(st.get("consecutive_failures", 0)) + 1
    _write_state({"consecutive_failures": n, "last_error": str(err)[:200],
                  "updated": datetime.now().astimezone().isoformat(timespec="seconds")})
    return n


def reset_failure() -> None:
    st = _read_state()
    if st.get("consecutive_failures"):
        _write_state({"consecutive_failures": 0, "last_error": None,
                      "updated": datetime.now().astimezone().isoformat(timespec="seconds")})


# ── 1회 실행 ─────────────────────────────────────────────────────────────
def _base_report(args, now: datetime) -> dict:
    jsonl_path = Path(args.jsonl_out) / f"{now.strftime('%Y-%m-%d')}.jsonl"
    g = _guard()
    return {
        "ts": now.isoformat(timespec="seconds"),
        "n_etfs": 0,
        "n_saved": 0,
        "db": not args.no_db,
        "jsonl": str(jsonl_path),
        "elapsed_ms": 0,
        "net_guard": "disabled" if (g is None or not g.enabled) else "ok",
    }


def run_once(args) -> dict:
    """스냅샷 1회. 예외를 올리지 않는다 — 실패는 report 의 error 필드(fail-open, 스펙 §1)."""
    t0 = time.time()
    now = datetime.now().astimezone()
    rep = _base_report(args, now)
    if not in_window(now, args.window, load_holidays()):
        rep["elapsed_ms"] = int((time.time() - t0) * 1000)
        rep["reason"] = "market_closed"          # 창 밖 — 아무 것도 안 함 + exit 0(정상, 스펙 §5)
        return rep

    g = _guard()
    try:
        raw = fetch(g)
        total_n, rows = parse_response(raw)      # 3분리: total_n=소스 수신, rows=파서 생성
    except Exception as e:  # noqa: BLE001 — 네트워크·파싱 실패 fail-open
        rep["elapsed_ms"] = int((time.time() - t0) * 1000)
        rep["error"] = f"{type(e).__name__}: {e}"[:200]
        return rep

    ts = datetime.now().astimezone()
    trade_date = ts.strftime("%Y-%m-%d")
    rep["ts"] = ts.isoformat(timespec="seconds")
    rep["n_etfs"] = len(rows)
    rep["n_source"] = total_n

    # jsonl 먼저(DB 다운에도 측정치 보존 — fail-open)
    line = {"ts": ts.isoformat(timespec="seconds"), "trade_date": trade_date,
            "n": len(rows), "rows": rows}
    jsonl_wrote = True
    try:
        jsonl_wrote = append_jsonl(Path(rep["jsonl"]), line)
    except OSError as e:
        rep["error"] = f"jsonl: {type(e).__name__}: {e}"[:200]
        rep["elapsed_ms"] = int((time.time() - t0) * 1000)
        return rep

    n_db = None
    if not args.no_db:
        try:
            conn = connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(DDL)
                conn.commit()
                ok = dq_claim is not None and dq_claim.claim_start(
                    RUNNER, TABLE, note=f"window={args.window}")
                n_db = store_rows(conn, to_db_rows(ts, trade_date, rows))
                if ok:
                    dq_claim.claim_finish(
                        RUNNER, source_rows=total_n, claimed_rows=len(rows),
                        persisted_rows=n_db,
                        note=f"lev_inv={len(rows)}/{total_n} window={args.window}")
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — DB 만 실패, jsonl 은 저장됨
            rep["error"] = f"db: {type(e).__name__}: {e}"[:200]
            rep["n_saved"] = len(rows) if jsonl_wrote else 0   # jsonl 저장분은 보존(3분리 정직)
            rep["elapsed_ms"] = int((time.time() - t0) * 1000)
            return rep
    rep["n_saved"] = n_db if n_db is not None else (len(rows) if jsonl_wrote else 0)
    rep["elapsed_ms"] = int((time.time() - t0) * 1000)

    # 콘솔 요약: |괴리| ≥ --min-premium-log 인 종목만(로그 폭주 방지 — 저장은 전량)
    for r in sorted(rows, key=lambda r: -abs(r["premium_pct"] or 0.0)):
        if r["premium_pct"] is not None and abs(r["premium_pct"]) >= args.min_premium_log:
            print(f"  {r['code']} {r['name']} premium={r['premium_pct']:+.2f}%")
    reset_failure()
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="레버리지/인버스 ETF 괴리율 스냅샷 수집(스펙 §1)")
    ap.add_argument("--once", action="store_true",
                    help="1회만 실행(기본. --interval 과 함께 주면 무시)")
    ap.add_argument("--interval", type=int, default=None,
                    help="반복 간격(초). 주면 --window 안에서 반복")
    ap.add_argument("--window", default="09:00-15:30", help="수집 창 HH:MM-HH:MM")
    ap.add_argument("--min-premium-log", type=float, default=0.3,
                    help="콘솔 요약 출력 기준 |괴리|%% (저장은 전량)")
    ap.add_argument("--no-db", action="store_true", help="DB 저장 생략(jsonl 만)")
    ap.add_argument("--jsonl-out", default=str(JSONL_DIR),
                    help="jsonl 저장 디렉터리(기본 data/etf_premium)")
    args = ap.parse_args(argv)

    loop = args.interval is not None and not args.once
    exit_code = 0
    while True:
        rep = run_once(args)
        print(json.dumps(rep, ensure_ascii=False), flush=True)
        if rep.get("error"):
            fails = bump_failure(rep["error"])
            if fails >= MAX_FAILS:
                exit_code = 1
                print(f"[etf_premium] 연속 {fails}회 실패 — exit 1(알림 대상)", file=sys.stderr)
        if not loop or rep.get("reason") == "market_closed":
            break
        time.sleep(max(1, args.interval))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
