#!/usr/bin/env python3
"""news_mention_features — news_stock_mentions → (종목, 거래일) 언급 피처 격자.

WHY (2026-11-19): 뉴스·감성 피처가 상수 0으로 죽어 있던 원인은 종목 연결 커버리지
(commit bb2912a 패널 재빌드 판정)였고, 그 앞 단계가 news_stock_collect 의 종목별 언급
원장이다. 이 러너는 그 원장을 ML 이 바로 쓰는 as-of 피처로 집계한다.
스펙 docs/spec_news_stock_pilot.md §2.

정의(스펙):
  mention_7d/30d/90d = 해당 거래일 **이전 누적, 당일 제외**(as-of) —
  published_date ∈ [trade_date - N일, trade_date) 인 언급 건수. 뉴스 없는 날은 0
  (정의상 "언급 없음" — 행이 존재하고 값이 0).
  거래일 격자 = market_data 의 distinct trade_date(해당 종목, --from~--to).
  언급이 전혀 없는 종목은 행 자체가 없다(모든 날 0 — 조인 시 coalesce 로 동일).

설계·근거:
  - published_date 는 pubDate(GMT)의 날짜 부분(UTC 기준)이다. KST 새벽 기사는 UTC 날짜가
    하루 앞당겨져 "하루 더 일찍 알려진 것"으로 센다 — **미래 정보를 보지 않는 보수적 방향**
    이라 as-of 규칙과 충돌하지 않는다(뒤집으면 KST 자정 직후 기사가 거래일 조회 시점에
    미래 정보가 되는 역전이 생긴다).
  - 멱등: PK(stock_code, trade_date) upsert — 재실행 시 같은 값으로 덮어쓴다.
  - 90일 창은 --from 보다 90일 앞의 언급이 필요하다 → 적재는 (from-90일)부터 조회.
  - published_date IS NULL 인 행은 어떤 창에도 넣지 않고 개수만 남긴다(추측 금지).
  - 자기신고(3분리): dq_claim — source_rows=사용된 언급 행수, claimed_rows=계산한 격자
    행수, persisted_rows=실제 upsert 행수.
  - --dry-run: 계산·집계까지만 하고 DB·자기신고에 아무것도 쓰지 않는다.

사용 (호스트 /usr/bin/python3):
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/news_mention_features.py --from 2026-08-19 --to 2026-11-18
"""
from __future__ import annotations

import argparse
import os
import sys
from bisect import bisect_left
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2  # noqa: E402

try:  # 자기신고(3분리) — 없으면 집계는 그대로 진행하고 자기신고만 생략한다
    from dq_claim import claim_start, claim_finish  # noqa: E402
except ImportError:
    claim_start = claim_finish = None

PG = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
          port=int(os.environ.get("POSTGRES_PORT", "5434")),
          dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
          user=os.environ.get("POSTGRES_USER", "stock_user"),
          password=os.environ.get("POSTGRES_PASSWORD", ""))

DDL = """
CREATE TABLE IF NOT EXISTS news_mention_features (
    stock_code  TEXT    NOT NULL,
    trade_date  DATE    NOT NULL,
    mention_7d  INTEGER NOT NULL DEFAULT 0,
    mention_30d INTEGER NOT NULL DEFAULT 0,
    mention_90d INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (stock_code, trade_date)
)
"""

MENTIONS_SQL = """
SELECT stock_code, published_date FROM news_stock_mentions
WHERE published_date IS NOT NULL AND published_date BETWEEN %s AND %s
"""

NULL_DATES_SQL = """
SELECT COUNT(*) FROM news_stock_mentions WHERE published_date IS NULL
"""

TRADES_SQL = """
SELECT DISTINCT trade_date FROM market_data
WHERE stock_code = %s AND trade_date BETWEEN %s AND %s
"""

UPSERT_SQL = """
INSERT INTO news_mention_features
    (stock_code, trade_date, mention_7d, mention_30d, mention_90d)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    mention_7d  = EXCLUDED.mention_7d,
    mention_30d = EXCLUDED.mention_30d,
    mention_90d = EXCLUDED.mention_90d
"""


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _between(sorted_dates, lo, hi):
    """[lo, hi) 구간 건수 — sorted_dates 는 오름차순 date 목록."""
    return bisect_left(sorted_dates, hi) - bisect_left(sorted_dates, lo)


def compute_grid(pub_dates, trade_dates):
    """(종목의 언급일, 거래일) → (7d, 30d, 90d) 언급 수 목록.

    창은 [trade_date - N일, trade_date) — **당일 제외**(as-of, 스펙 §2).
    """
    rows = []
    for td in trade_dates:
        rows.append((
            _between(pub_dates, td - timedelta(days=7), td),
            _between(pub_dates, td - timedelta(days=30), td),
            _between(pub_dates, td - timedelta(days=90), td),
        ))
    return rows


def main():
    ap = argparse.ArgumentParser(description="종목별 뉴스 언급 피처 집계(파일럿)")
    ap.add_argument("--from", required=True, dest="d_from", help="시작일 YYYY-MM-DD")
    ap.add_argument("--to", required=True, dest="d_to", help="종료일 YYYY-MM-DD(포함)")
    ap.add_argument("--dry-run", action="store_true",
                    help="계산까지만 하고 DB·자기신고에 아무것도 쓰지 않는다")
    a = ap.parse_args()

    try:
        d_from = datetime.strptime(a.d_from, "%Y-%m-%d").date()
        d_to = datetime.strptime(a.d_to, "%Y-%m-%d").date()
    except ValueError:
        log(f"날짜 형식 오류: --from={a.d_from} --to={a.d_to} (YYYY-MM-DD)")
        return 2
    if d_from > d_to:
        log(f"기간 오류: from({d_from}) > to({d_to})")
        return 2

    try:
        conn = psycopg2.connect(**PG)
    except psycopg2.Error as exc:
        log(f"DB 연결 실패: {exc}")
        return 2
    cur = conn.cursor()

    if not a.dry_run:
        cur.execute(DDL)
        conn.commit()
        if claim_start is not None:
            claim_start("news_mention_features", "news_mention_features",
                        note=f"{d_from}~{d_to}")

    # 언급 적재: 90일 창이 from-90일까지 필요하다.
    # 테이블이 아직 없으면(수집 전) 트랜잭션을 되돌리고 0행으로 처리한다 — UndefinedTable 로
    # 끊긴 트랜잭션을 rollback 하지 않으면 다음 질의가 InFailedSqlTransaction 으로 죽는다.
    try:
        cur.execute(MENTIONS_SQL, (d_from - timedelta(days=90), d_to))
        mention_rows = cur.fetchall()
        cur.execute(NULL_DATES_SQL)
        null_dates = int(cur.fetchone()[0])
    except psycopg2.errors.UndefinedTable:
        conn.rollback()
        mention_rows = []
        null_dates = 0
        log("news_stock_mentions 테이블 없음(아직 수집 전?) — 0행으로 처리")

    by_code = {}
    for code, pub in mention_rows:
        by_code.setdefault(code, []).append(pub)
    for dates in by_code.values():
        dates.sort()

    claimed = written = no_market = 0
    for code in sorted(by_code):
        cur.execute(TRADES_SQL, (code, d_from, d_to))
        trade_dates = sorted(row[0] for row in cur.fetchall())
        if not trade_dates:
            no_market += 1
            continue
        counts = compute_grid(by_code[code], trade_dates)
        claimed += len(trade_dates)
        if a.dry_run:
            continue
        batch = [(code, td, c7, c30, c90)
                 for td, (c7, c30, c90) in zip(trade_dates, counts)]
        cur.executemany(UPSERT_SQL, batch)
        written += max(int(cur.rowcount or 0), 0)
        conn.commit()

    if claim_finish is not None and not a.dry_run:
        claim_finish("news_mention_features",
                     source_rows=len(mention_rows), claimed_rows=claimed,
                     persisted_rows=written,
                     note=(f"stocks={len(by_code)} no_market={no_market} "
                           f"null_dates={null_dates} from={d_from} to={d_to}"))

    log(f"완료: 언급 종목 {len(by_code)} / 격자 행 {claimed} / 실제 upsert {written} / "
        f"사용 언급 {len(mention_rows)}행 (NULL 날짜 {null_dates}행 제외, "
        f"market_data 없는 종목 {no_market})")
    if not mention_rows and not null_dates:
        log("힌트: news_stock_mentions 가 비어 있다 — news_stock_collect 를 먼저 돌려야 한다")
    if a.dry_run:
        log("dry-run: DB·자기신고에 아무것도 쓰지 않았다")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
