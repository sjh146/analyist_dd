#!/usr/bin/env python3
"""KIS 투자자 수급 백필 러너 — 종목별 외국인/기관/개인 순매수 + 지분율.

사용 (호스트, services/kis-collector 기준):
  set -a; . ../../.env; set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  python3 ../../scripts/kis_supply_backfill.py                      # 유동성 상위 30종목 × 최근 60영업일
  python3 ../../scripts/kis_supply_backfill.py --stocks 005930,000660 --days 90
  python3 ../../scripts/kis_supply_backfill.py --dry-run            # DB/HTTP 없이 흐름만
  KIS_SUPPLY_MAX_CALLS=400 python3 ...

설계 근거
  · KRX OpenAPI 투자자별 거래실적은 이 계정에 미승인(401 Unauthorized API Call)
    → KIS 가 유일한 경로. 관련 TR/필드 실측은 kis_app.client.supply_client 참조.
  · KIS 응답 1콜 = **최대 30행** → 60영업일 = 종목당 2콜 (호출 수 = 종목수 × (페이지 + 1))
  · 지분율은 현재 스냅샷만 제공 → 종목당 1콜, 최신 거래일 1행.
  · 진행파일(data/kis/supply_progress_<from>_<to>.txt)로 재개. 성공 종목만 기록한다.
  · 자격증명 미설정 시 fail-fast(exit 2) — 조용히 0건 적재하지 않는다.
  · 레이트리밋(EGW00123 일일 / EGW00124 분당 / EGW00133 토큰) 감지 시 즉시 중단(exit 3).
"""
import argparse
import os
import random
import sys
from datetime import date, datetime, timedelta

import psycopg2

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "services", "kis-collector"))

from kis_app.client.kis_client import KisApiError, RATE_LIMIT_CODES  # noqa: E402
from kis_app.client.supply_client import SupplyClient  # noqa: E402
from kis_app.collectors.supply_collector import (  # noqa: E402
    SupplyCollector, parse_investor_flow,
)
from kis_app.config import Config  # noqa: E402

PROJ = os.environ.get("PROJ_DIR", "/home/dduckbeagy/analyist_dd")
PG = dict(
    host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    port=int(os.environ.get("POSTGRES_PORT", "5434")),
    user=os.environ.get("POSTGRES_USER", "stock_user"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
    dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
)

# 검증 대상 종목은 언제나 포함한다 (리더 피처 검증에 쓰인다)
ALWAYS_INCLUDE = ["005930"]


def latest_trading_date(conn):
    cur = conn.cursor()
    cur.execute("SELECT MAX(trade_date) FROM market_data")
    row = cur.fetchone()
    cur.close()
    return row[0]


def liquidity_universe(conn, top_n, lookback_days=20):
    """최근 N영업일 평균 거래대금 상위 종목 (실제 주식만, SPAC 제외).

    market_data.trading_value 는 최신일 다수가 NULL 이므로 최근 창 전체로 평균한다.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT md.stock_code, s.stock_name,
               AVG(COALESCE(md.trading_value, md.close_price * md.volume)) AS avg_tv
        FROM market_data md
        JOIN stocks s ON s.stock_code = md.stock_code
        WHERE s.market IN ('KOSPI', 'KOSDAQ')
          AND s.instrument_type = 'STOCK'
          AND s.stock_name NOT LIKE '%%스팩%%'
          AND md.trade_date > (SELECT MAX(trade_date) - %s FROM market_data)
        GROUP BY md.stock_code, s.stock_name
        HAVING COUNT(*) >= 10
        ORDER BY avg_tv DESC NULLS LAST
        LIMIT %s
    """, (lookback_days * 3, top_n))
    rows = [(r[0], r[1]) for r in cur.fetchall()]
    cur.close()
    return rows


def resolve_universe(conn, args):
    if args.stocks:
        codes = [c.strip() for c in args.stocks.split(",") if c.strip()]
        cur = conn.cursor()
        cur.execute("SELECT stock_code, stock_name FROM stocks WHERE stock_code = ANY(%s)",
                    (codes,))
        found = {r[0]: r[1] for r in cur.fetchall()}
        cur.close()
        missing = [c for c in codes if c not in found]
        if missing:
            print(f"  경고: stocks 에 없는 코드 {missing} — 건너뜀", flush=True)
        return [(c, found[c]) for c in codes if c in found]
    return liquidity_universe(conn, args.top)


def progress_path(d_from, d_to):
    return os.path.join(PROJ, "data", "kis",
                        f"supply_progress_{d_from}_{d_to}.txt")


def load_done(path):
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {l.strip() for l in f if l.strip()}


def save_done(path, code):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(code + "\n")


def main():
    ap = argparse.ArgumentParser(description="KIS 종목별 투자자 수급 수집")
    ap.add_argument("--from", dest="d_from", help="YYYYMMDD (기본: 최신거래일-90일)")
    ap.add_argument("--to", dest="d_to", help="YYYYMMDD (기본: 최신거래일)")
    ap.add_argument("--days", type=int, default=60, help="목표 영업일 수 (기본 60)")
    ap.add_argument("--top", type=int, default=30, help="유동성 상위 N종목 (기본 30)")
    ap.add_argument("--stocks", help="쉼표 구분 종목코드 (지정 시 --top 무시)")
    ap.add_argument("--limit", type=int, default=0, help="점검용 처리 종목 수 제한")
    ap.add_argument("--delay", type=float,
                    default=float(os.environ.get("KIS_REQUEST_DELAY", "1.0")),
                    help="호출 간 지연(초). 기본 1.0 (실전 계좌 초당 20건 한도에 여유)")
    ap.add_argument("--max-pages", type=int, default=6, help="종목당 수급 페이지 상한")
    ap.add_argument("--dry-run", action="store_true", help="DB 쓰기·HTTP 없이 흐름 점검")
    args = ap.parse_args()

    cfg = Config()
    if not args.dry_run and (not cfg.KIS_APP_KEY or not cfg.KIS_APP_SECRET):
        print("KIS_APP_KEY/KIS_APP_SECRET 미설정 — .env 를 채운 뒤 다시 실행하세요.", flush=True)
        return 2

    conn = psycopg2.connect(**PG) if not args.dry_run else None
    if conn is None:
        # dry-run: 저장소 없이 파서만 확인
        print("dry-run: DB 연결 생략", flush=True)
        return 0

    as_of = latest_trading_date(conn)
    if as_of is None:
        print("market_data 가 비었습니다 — 시세 수집을 먼저 하세요.", flush=True)
        return 1

    if args.d_from and args.d_to:
        start = datetime.strptime(args.d_from, "%Y%m%d").date()
        end = datetime.strptime(args.d_to, "%Y%m%d").date()
    else:
        end = as_of
        # 영업일 60일 ≈ 역일 90일 (주말·휴장 감안). 30행/콜이라 여유 있게 잡아도
        # 종목당 페이지 상한(--max-pages)이 실제 호출을 제한한다.
        start = end - timedelta(days=int(args.days * 1.6) + 10)

    universe = resolve_universe(conn, args)
    codes = {c for c, _ in universe}
    for extra in ALWAYS_INCLUDE:
        if extra not in codes:
            cur = conn.cursor()
            cur.execute("SELECT stock_name FROM stocks WHERE stock_code = %s", (extra,))
            row = cur.fetchone()
            cur.close()
            if row:
                universe.append((extra, row[0]))
    if args.limit:
        universe = universe[:args.limit]
    if not universe:
        print("유니버스 없음 — stocks 테이블을 확인하세요.", flush=True)
        return 1

    d_from, d_to = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    ppath = progress_path(d_from, d_to)
    done = load_done(ppath)
    todo = [(c, n) for c, n in universe if c not in done]

    print(f"기준 거래일(as-of) = {as_of} / 구간 {d_from}~{d_to} (목표 {args.days}영업일)", flush=True)
    print(f"유니버스 {len(universe)}종목 (완료 {len(done)}, 남음 {len(todo)}) / "
          f"지연 {args.delay}s / 종목당 최대 {args.max_pages + 1}콜", flush=True)

    client = SupplyClient(cfg.KIS_APP_KEY, cfg.KIS_APP_SECRET, cfg.KIS_BASE_URL,
                          delay=args.delay, jitter=0.3,
                          token_path=cfg.KIS_TOKEN_PATH, dry_run=args.dry_run)
    coll = SupplyCollector(client, conn)
    coll.ensure_tables()
    # 자기신고 기준선 (실제 적재량 델타 계산용) — 이 러너는 2개 테이블에 쓴다.
    _before = {t: _count_rows(conn, t) for t in ("foreign_institutional", "ownership")}

    max_calls = int(os.environ.get("KIS_SUPPLY_MAX_CALLS", "500"))
    calls = 0
    ok = fail = nodata = flow_rows = own_rows = src_rows = 0
    quota_hit = False

    for idx, (code, name) in enumerate(todo, start=1):
        if calls >= max_calls:
            print(f"실행당 호출 상한({max_calls}) 도달 — 중단, 다음 실행에서 이어서", flush=True)
            break
        try:
            rows = client.get_investor_flow_all(code, "J", d_from, d_to,
                                                max_pages=args.max_pages)
            calls += max(1, (len(rows) + 29) // 30)
            src_rows += len(rows)
            n = coll.save_flows(code, parse_investor_flow({"output": rows}))
            flow_rows += n
            if not rows:
                nodata += 1
                print(f"  [{idx}/{len(todo)}] {code} {name} — 수급 데이터 없음", flush=True)
                continue
            # 지분율 스냅샷은 '그 종목의 수급이 확인된 최신 거래일'에 귀속시킨다
            stock_as_of = min(max(
                datetime.strptime(r["stck_bsop_date"], "%Y%m%d").date() for r in rows), as_of)
            own = coll.collect_ownership(code, stock_as_of, "J")
            calls += 1
            own_rows += own
            ok += 1
            save_done(ppath, code)
            if idx % 5 == 0 or idx == len(todo):
                print(f"  [{idx}/{len(todo)}] {code} {name}: 수급 {n}행, "
                      f"지분율 {own}행 (as-of {stock_as_of})", flush=True)
        except KisApiError as e:
            fail += 1
            print(f"  [{idx}/{len(todo)}] {code} {name} 실패: {e}", flush=True)
            if e.msg_cd in RATE_LIMIT_CODES or e.credential_error:
                print(f"  → 한도/자격증명 오류({e.msg_cd}) — 즉시 중단", flush=True)
                quota_hit = True
                break
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [{idx}/{len(todo)}] {code} {name} 실패: {type(e).__name__}: {e}", flush=True)

    # ── 결과 집계 ──────────────────────────────────────────────────────────
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_date), MAX(trade_date)
                   FROM foreign_institutional""")
    fc, fr, fmin, fmax = cur.fetchone()
    cur.execute("""SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_date), MAX(trade_date),
                          COUNT(foreign_ownership_pct)
                   FROM ownership""")
    oc, orr, omin, omax, ocov = cur.fetchone()
    cur.close()
    print(f"\n완료: ok={ok} fail={fail} no_data={nodata} 호출≈{calls}"
          f"{' (한도 중단)' if quota_hit else ''}", flush=True)
    print(f"  foreign_institutional: {fc}종목 {fr}행 ({fmin}~{fmax})", flush=True)
    print(f"  ownership:             {oc}종목 {orr}행 ({omin}~{omax}), "
          f"foreign_ownership_pct 값 있음 {ocov}행", flush=True)

    # ── 자기신고: 러너가 "적재했다"고 믿는 수(flow_rows/own_rows)와 실제 델타를 함께 남긴다.
    # WHY: 2026-09-24 이 러너 계열이 파서 키 불일치로 +0행을 적재하고도 exit 0 으로 끝나
    # 조용히 스킵됐다. 주장과 실제를 남기면 dq_claim_gap 메트릭이 그 유형을 잡는다.
    claim_failed = _record_claims(conn, _before, flow_rows, own_rows, src_rows,
                                  ok, fail, nodata, calls)
    conn.close()
    # 종료코드 4 = 자기신고상 명확한 파서/적재 실패(소스는 줬는데 저장 0).
    # 3=호출 한도 중단, 1=API 실패. 크론 로그에서 '조용한 실패'를 구분하기 위한 값이다.
    if claim_failed:
        return 4
    if quota_hit:
        return 3
    return 0 if fail == 0 else 1


def _count_rows(conn, table):
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM " + table)  # noqa: S608 - 내부 상수
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def _record_claims(conn, before, flow_rows, own_rows, src_rows, ok, fail, nodata, calls):
    """(runner, table) 당 1행씩 자기신고를 남긴다.

    source_rows 를 함께 남기는 이유: 같은 구간을 재수집하면 upsert 로 중복이 걸려
    claimed > 0 인데 DB 델타는 0 이 **정상 발생**한다(실측: 주장 44 / 신규 0).
    진짜 실패는 ``source > 0 AND claimed == 0`` — API 는 줬는데 로더가 저장을 못 한 경우다.
    """
    try:
        from dq_claim import record_claim
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from dq_claim import record_claim

    note = f"ok={ok} fail={fail} no_data={nodata} calls≈{calls}"
    pairs = [
        ("foreign_institutional", flow_rows, src_rows),
        ("ownership", own_rows, own_rows),
    ]
    parse_failed_any = False
    for table, claimed, src in pairs:
        after = _count_rows(conn, table)
        persisted = after - before.get(table, 0)
        record_claim(conn, "kis_supply_backfill", table,
                     claimed_rows=claimed, persisted_rows=persisted, source_rows=src,
                     note=f"{note} rows {before.get(table, 0)}->{after}")
        if src > 0 and claimed == 0:
            state = "★파서/적재 실패(소스는 줬는데 저장 0)"
            parse_failed_any = True
        elif claimed != persisted:
            state = "신규 없음(중복 재수집이면 정상)"
        else:
            state = "신규 적재 일치"
        print(f"  자기신고[{table}]: 소스 {src} / 저장 {claimed} / 신규 {persisted} — {state}",
              flush=True)
    # 명확한 실패만 exit code 로 신호한다(중복 재수집은 정상이므로 실패로 만들지 않는다).
    return parse_failed_any


if __name__ == "__main__":
    random.seed()
    sys.exit(main())
