#!/usr/bin/env python3
"""KIS 일별 투자자 매매동향(FHPTJ04160001)으로 foreign_institutional 의 과거 구간을 연장한다.

사용 (호스트):
  set -a; . ./.env; set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/kis_supply_extend_history.py --stocks-file data/kis/train_universe_200.txt
  /usr/bin/python3 scripts/kis_supply_extend_history.py --stocks 005930,000660 --target-days 250
  /usr/bin/python3 scripts/kis_supply_extend_history.py --stocks-file <f> --limit 5 --dry-run

배경 (2026-09-24 실측 — 왜 이 스크립트가 필요한가)
  · ``inquire-investor`` (FHKST01010900) 는 ``FID_INPUT_DATE_1``/``FID_INPUT_DATE_2`` 를 **무시**하고
    **항상 최신 30영업일**만 돌려준다. 실측: 20250101~20250201 을 요청해도 20260812~20260923 이 왔다.
    → 그 TR 만으로는 30영업일이 상한이고, 페이지네이션이 성립하지 않는다.
  · ``investor-trade-by-stock-daily`` (FHPTJ04160001) 는 ``FID_INPUT_DATE_1`` 을 **창의 끝(포함)** 으로
    쓰고 ``FID_INPUT_DATE_2 > FID_INPUT_DATE_1`` 이어야 응답한다(같으면 빈 응답). 실측:
      d1=20260923 → 20260812~20260923 / d1=20260811 → 20260630~20260811 / d1=20260630 → 20260518~20260630
    → d1 을 'DB 최소일 -1일' 로 밀면 30영업일씩 과거로 파고들 수 있다.
  · 필드명·단위가 동일하다(``frgn_ntby_tr_pbmn`` 등 — 005930 20260923 값이 두 TR 에서 일치) →
    ``kis_app.collectors.supply_collector.parse_investor_flow`` 를 그대로 재사용한다.

원칙
  · 이미 있는 행보다 **오래된 행만** 적재한다(기존 구간 무변경, upsert 멱등).
  · DB 에 아직 없는 종목(신규 유니버스)은 **최신 거래일부터** 시작해 과거로 내려간다.
  · 진행파일(data/kis/supply_extend_progress_<target>.txt)로 재개 — 목표를 채운 종목만 기록한다.
  · 레이트리밋/자격증명 오류는 즉시 중단(exit 3). 종목당 실패는 건너뛰고 계속.
  · 상장 전 구간은 KIS 가 all-zero 행으로 패딩해 주므로 저장 단계에서 걸러진다
    (``SupplyCollector.save_flows`` 가 market_data 에 없는 날짜를 버린다) → 그런 종목은 자동 종료.
"""
import argparse
import os
import sys
from datetime import date, timedelta

import psycopg2

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "services", "kis-collector"))

from kis_app.client.kis_client import KisApiError, RATE_LIMIT_CODES  # noqa: E402
from kis_app.client.supply_client import SupplyClient  # noqa: E402
from kis_app.collectors.supply_collector import (  # noqa: E402
    SupplyCollector, parse_investor_flow,
)
from kis_app.config import Config  # noqa: E402

DAILY_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
DAILY_TR_ID = "FHPTJ04160001"

PROJ = os.environ.get("PROJ_DIR", _HERE + "/..")
PG = dict(
    host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    port=int(os.environ.get("POSTGRES_PORT", "5434")),
    user=os.environ.get("POSTGRES_USER", "stock_user"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
    dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
)


def coverage(conn, code):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(trade_date) FROM foreign_institutional "
                "WHERE stock_code = %s", (code,))
    n, mn = cur.fetchone()
    cur.close()
    return n, mn


def latest_trading_date(conn):
    cur = conn.cursor()
    cur.execute("SELECT MAX(trade_date) FROM market_data")
    d = cur.fetchone()[0]
    cur.close()
    return d


def load_done(path):
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {l.strip() for l in f if l.strip()}


def read_codes(args, conn):
    if args.stocks_file:
        with open(args.stocks_file) as f:
            return [l.strip() for l in f if l.strip()]
    if args.stocks:
        return [c.strip() for c in args.stocks.split(",") if c.strip()]
    cur = conn.cursor()
    cur.execute("SELECT stock_code FROM foreign_institutional "
                "GROUP BY stock_code ORDER BY stock_code")
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    return codes


def main():
    ap = argparse.ArgumentParser(description="KIS 일별 투자자 매매동향으로 수급 과거 구간 연장")
    ap.add_argument("--stocks", help="쉼표 구분 종목코드")
    ap.add_argument("--stocks-file", help="종목코드 파일(한 줄에 하나)")
    ap.add_argument("--target-days", type=int, default=250, help="종목당 목표 영업일 (기본 250)")
    ap.add_argument("--max-pages", type=int, default=12, help="종목당 과거 페이지 상한(30영업일/페이지)")
    ap.add_argument("--max-calls", type=int,
                    default=int(os.environ.get("KIS_SUPPLY_MAX_CALLS", "2600")),
                    help="실행당 호출 상한")
    ap.add_argument("--delay", type=float,
                    default=float(os.environ.get("KIS_REQUEST_DELAY", "1.5")))
    ap.add_argument("--limit", type=int, default=0, help="점검용 종목 수 제한")
    ap.add_argument("--dry-run", action="store_true", help="HTTP/DB 쓰기 없이 대상만 출력")
    args = ap.parse_args()

    cfg = Config()
    if not cfg.KIS_APP_KEY or not cfg.KIS_APP_SECRET:
        print("KIS_APP_KEY/KIS_APP_SECRET 미설정 — .env 확인", flush=True)
        return 2

    conn = psycopg2.connect(**PG)
    codes = read_codes(args, conn)
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        print("대상 종목 없음", flush=True)
        return 1

    as_of = latest_trading_date(conn)
    if as_of is None:
        print("market_data 가 비었습니다 — 시세 수집 먼저.", flush=True)
        return 1

    ppath = os.path.join(PROJ, "data", "kis",
                         f"supply_extend_progress_{args.target_days}.txt")
    done = load_done(ppath)

    plan = []
    for c in codes:
        n, mn = coverage(conn, c)
        if c in done or n >= args.target_days:
            continue
        plan.append((c, n, mn))
    print(f"기준 거래일 {as_of} / 대상 {len(codes)}종목 / 목표 {args.target_days}영업일 / "
          f"완료 {len(done)} / 남음 {len(plan)}", flush=True)
    if args.dry_run:
        for c, n, mn in plan[:20]:
            print(f"  {c}: 현재 {n}행 (min {mn}) → 목표 {args.target_days}", flush=True)
        return 0

    client = SupplyClient(cfg.KIS_APP_KEY, cfg.KIS_APP_SECRET, cfg.KIS_BASE_URL,
                          delay=args.delay, jitter=0.3, token_path=cfg.KIS_TOKEN_PATH)
    coll = SupplyCollector(client, conn)
    coll.ensure_tables()

    calls = 0
    added_total = 0
    src_total = 0   # 소스 API 가 준 행수 — 파서 실패(수신>0, 저장 0)를 오탐 없이 판정하기 위해 필요
    hit_limit = False
    # 자기신고 기준선: 이 실행이 시작될 때의 테이블 행수 (실제 적재량 델타 계산용)
    rows_before = _count_rows(conn, "foreign_institutional")
    for idx, (code, n, mn) in enumerate(plan, start=1):
        if calls >= args.max_calls:
            print(f"실행당 호출 상한({args.max_calls}) 도달 — 중단, 다음 실행에서 재개", flush=True)
            hit_limit = True
            break
        cutoff = mn.isoformat() if mn else None
        anchor = (mn - timedelta(days=1)) if mn else as_of
        got, pages = 0, 0
        while pages < args.max_pages and (n + got) < args.target_days:
            if calls >= args.max_calls:
                hit_limit = True
                break
            d1 = anchor
            d2 = d1 + timedelta(days=1)
            try:
                resp = client._quotes(DAILY_PATH, DAILY_TR_ID, {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": str(code),
                    "FID_INPUT_DATE_1": d1.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": d2.strftime("%Y%m%d"),
                    "FID_ORG_ADJ_PRC": "0",
                    "FID_ETC_CLS_CODE": "0",
                })
                calls += 1
            except KisApiError as e:
                print(f"  [{idx}/{len(plan)}] {code} 오류 {e}", flush=True)
                if e.msg_cd in RATE_LIMIT_CODES or e.credential_error:
                    print(f"  → 한도/자격증명({e.msg_cd}) — 즉시 중단", flush=True)
                    _summary(conn, args.target_days, added_total, calls)
                    return 3
                break
            parsed = parse_investor_flow(resp)
            src_total += len(parsed)   # 소스가 준 행수(자기신고 source_rows)
            older = [r for r in parsed if cutoff is None or r["trade_date"] < cutoff]
            if not older:
                break
            saved = coll.save_flows(code, older)
            got += saved
            pages += 1
            oldest = min(r["trade_date"] for r in older)
            cutoff = oldest
            anchor = date.fromisoformat(oldest) - timedelta(days=1)
            # 저장된 게 하나도 없으면(=전부 상장 전 패딩) 더 내려가도 의미가 없다
            if saved == 0 and pages >= 1:
                break
        added_total += got
        if got:
            print(f"  [{idx}/{len(plan)}] {code}: +{got}행 (총 {n + got}행, 최소일 {cutoff})", flush=True)
        if (n + got) >= args.target_days:
            os.makedirs(os.path.dirname(ppath), exist_ok=True)
            with open(ppath, "a") as f:
                f.write(code + "\n")
        elif got == 0 and n > 0:
            # 이미 있는 구간보다 과거 데이터가 없는 종목(상장 시점 한계) — 재시도 무의미.
            # n == 0 (아무것도 못 받은 경우)은 **완료로 기록하지 않는다** — 실패를 완료로
            # 남기면 다음 실행이 그 종목을 영구히 건너뛴다(실측: 이 버그로 011330 이 1회 실패 후 스킵됨).
            os.makedirs(os.path.dirname(ppath), exist_ok=True)
            with open(ppath, "a") as f:
                f.write(code + "\n")

    _summary(conn, args.target_days, added_total, calls)
    claim_failed = False
    if plan:
        claim_failed = _record_claim(conn, args.target_days, added_total, calls,
                                     rows_before, src_total)
    conn.close()
    # 종료코드: 3=호출 한도 중단, 4=자기신고 불일치(소스는 줬는데 저장 0 = 파서/적재 버그).
    # 4 를 따로 두는 이유: 크론 로그·알림에서 '한도 중단'과 '조용한 실패'를 구분해야 한다.
    if claim_failed:
        return 4
    return 3 if hit_limit else 0


def _count_rows(conn, table):
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM " + table)  # noqa: S608 - 내부 상수
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def _record_claim(conn, target_days, added_total, calls, rows_before, src_total):
    """러너 자기신고 — 주장·실제 델타·**소스 수신량**을 남기고, 명확한 실패를 반환한다.

    WHY: 2026-09-24 이 러너가 파서 키 불일치(output vs output2)로 **+0행을 적재하고도
    exit 0** 으로 끝났고, 진행 파일에는 완료로 기록돼 그 종목이 조용히 스킵됐다.
    2026-09-25 위임 리뷰(opencode)가 **source_rows 를 넘기지 않아 parse_failure 판정이
    항상 0** 이라는 결함을 지적했고(그래서 RunnerParseFailure 알림이 이 러너에 절대 안 울렸다),
    이 함수가 수정본이다.
    반환: True = 파서/적재 실패(수신>0 인데 저장 0) → 호출부가 exit 4 로 신호.
    """
    try:
        from dq_claim import record_claim
    except ImportError:
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from dq_claim import record_claim

    after = _count_rows(conn, "foreign_institutional")
    delta = after - rows_before
    record_claim(
        conn, "kis_supply_extend_history", "foreign_institutional",
        claimed_rows=added_total, persisted_rows=delta, source_rows=src_total,
        note=f"target_days={target_days} calls={calls} rows {rows_before}->{after}",
    )
    verdict = "일치" if added_total == delta else "불일치 — 확인 필요"
    print(f"  자기신고: 소스 {src_total} / 저장 {added_total} / 신규 {delta} ({verdict})",
          flush=True)
    parse_failed = src_total > 0 and added_total == 0
    if parse_failed:
        print("  자기신고: ★파서/적재 실패 — 소스에서 행을 받았는데 저장이 0행이다", flush=True)
    return parse_failed


def _summary(conn, target_days, added_total, calls):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_date), MAX(trade_date) "
                "FROM foreign_institutional")
    sc, rows, dmin, dmax = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM (SELECT stock_code FROM foreign_institutional "
                "GROUP BY stock_code HAVING COUNT(*) >= %s) t", (target_days,))
    full = cur.fetchone()[0]
    cur.close()
    print(f"\n연장 완료: +{added_total}행 / 호출 {calls}회", flush=True)
    print(f"  foreign_institutional: {sc}종목 {rows}행 ({dmin}~{dmax}) / "
          f"{target_days}영업일 이상 {full}종목", flush=True)


if __name__ == "__main__":
    sys.exit(main())
