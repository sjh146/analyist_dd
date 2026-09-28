#!/usr/bin/env python3
"""R3 유니버스 커버리지 프로브 (읽기 전용 — KIS 호출 0회, DB SELECT 2~3회).

WHY (2026-09-29 06:0x 실측): R3 의 check 는 `SELECT COUNT(DISTINCT stock_code) FROM
foreign_institutional` 이었는데 그 **분모가 '테이블 전체'** 다(실측 892종목). 테이블에는
유니버스 밖 코드(과거 수집분)가 섞여 있어, 유니버스 800 중 267종목의 수급이 통째로 비어도
'892 >= 500 충족'으로 통과한다 — 분모 정의가 곧 판정이다(스킬 교훈: R19 의 옛 check 와 같은 함정).
목표는 '모델 유니버스 800종목의 수급 커버리지'(R3 title) 이므로 분모를 유니버스로 고정한다.

유니버스 정의(항목 note 와 동일): 비ETF·ETN(instrument_type='STOCK'), 최근 250행 이상 시세 보유,
시가총액 상위 800 → `data/kis/supply_universe_800.txt` (800코드, gitignore 대상이라 사라질 수 있다).
파일이 없거나 `--universe none` 이면 **같은 정의의 SQL 로 재생성**한다(실측 재현율 786/800=98.3%).

사용:
  cd /home/jhshi/analyist_dd && POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 \
    /usr/bin/python3 scripts/r3_universe_coverage.py [--universe <path>|none]
stdout **마지막 수치** = 유니버스 ∩ foreign_institutional 종목수 (구동기 check 판정용).
종료코드: 0 정상 / 1 DB·유니버스 오류(조용히 넘기면 판정이 실명한다).
"""
import argparse
import os
import sys

import psycopg2

PROJ = os.environ.get("PROJ_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSE_FILE = os.path.join(PROJ, "data", "kis", "supply_universe_800.txt")

# 항목 note 에 적힌 재생성 쿼리와 동일(재현율 786/800 실측). 파일이 없을 때만 쓴다.
UNIVERSE_SQL = """
SELECT s.stock_code
FROM stocks s
JOIN market_data m ON m.stock_code = s.stock_code
WHERE s.instrument_type = 'STOCK'
GROUP BY s.stock_code
HAVING COUNT(*) >= 250
ORDER BY MAX(s.market_cap) DESC NULLS LAST
LIMIT 800
"""

PG = {
    "host": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    "port": int(os.environ.get("POSTGRES_PORT", "5434")),
    "user": os.environ.get("POSTGRES_USER", "stock_user"),
    "password": os.environ.get("POSTGRES_PASSWORD", ""),
    "dbname": os.environ.get("POSTGRES_DB", "stock_trading"),
}


def load_universe(conn, spec):
    """(codes, 출처 표기). 파일이 있으면 파일, 없으면 SQL 재생성."""
    if spec != "none":
        path = UNIVERSE_FILE if spec is None else spec
        try:
            with open(path, encoding="utf-8") as f:
                codes = [ln.strip() for ln in f if ln.strip()]
            if codes:
                return sorted(set(codes)), f"파일 {os.path.relpath(path, PROJ)}"
        except OSError:
            if spec is not None:
                raise
    cur = conn.cursor()
    cur.execute(UNIVERSE_SQL)
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    return sorted(set(codes)), "SQL 재생성(파일 없음)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default=None,
                    help="유니버스 파일 경로, 또는 'none' 이면 DB 에서 재생성")
    args = ap.parse_args()

    try:
        conn = psycopg2.connect(**PG)
    except Exception as exc:  # noqa: BLE001 - 조회 실패는 실명이므로 rc=1 로 크게 남긴다
        print(f"DB 연결 실패: {type(exc).__name__}: {exc}", flush=True)
        return 1
    try:
        uni, src = load_universe(conn, args.universe)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT stock_code), COUNT(*), MAX(trade_date) "
                    "FROM foreign_institutional")
        total, rows, as_of = cur.fetchone()
        cur.execute("SELECT COUNT(DISTINCT stock_code) FROM foreign_institutional "
                    "WHERE stock_code = ANY(%s)", (uni,))
        covered = cur.fetchone()[0]
        cur.close()
    finally:
        conn.close()

    if not uni:
        print("유니버스 0코드 — 파일·SQL 확인 필요", flush=True)
        return 1
    miss = len(uni) - covered
    print(f"유니버스 {len(uni)}종목({src}) / 수급 보유 {covered} / 미커버 {miss}"
          f" / 테이블 전체 {total}종목 {rows:,}행 (최신일 {as_of})")
    print(f"커버리지 {covered}")   # ← stdout 마지막 수치 = 판정값
    return 0


if __name__ == "__main__":
    sys.exit(main())
