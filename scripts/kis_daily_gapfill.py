#!/usr/bin/env python3
"""kis_daily_gapfill — 하루치 일봉에서 **비어 있는 종목만** KIS 로 채운다.

WHY (실측 2026-11-09 19:54)
  18:55 일봉 수집(KIS, 종목당 1콜)이 WSL 재부팅으로 553/3,979 에서 끊겨 당일 적재가
  1,260행(정상 2,986)으로 남았다. 그런데 공실 점검은 **절대 임계 1,000** 만 봤으므로
  '공실 없음'으로 통과했고, 다음날 04:15 백필도 이 날을 건드리지 않을 상태였다.
  KRX OpenAPI 는 당일 데이터를 주지 않으므로(실측 21:16, 20261109 → 0행) 복구 경로는 KIS 뿐이다.

  재수집은 종목당 1콜이라 전 종목(3,979)을 다시 돌리면 2.4시간이다. 이미 있는 종목을 빼고
  **빈 종목만** 유동성 순으로 채우면 같은 시간에 훨씬 많은 구멍을 메운다.

사용
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 PROJ_DIR=/home/jhshi/analyist_dd
  /usr/bin/python3 scripts/kis_daily_gapfill.py --date 2026-11-09 --limit 600 --dry-run
  /usr/bin/python3 scripts/kis_daily_gapfill.py --date 2026-11-09 --limit 600

종료코드: 0 = 목표(정상일 대비 60%) 도달, 3 = 부분 복구(다음 실행에서 재개), 2 = 사용/환경 오류.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
KIS_DIR = os.path.join(PROJ, "services", "kis-collector")


def _connect():
    import psycopg2

    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = int(os.environ.get("POSTGRES_PORT", "5434"))
    if host in ("postgres", "db"):
        host, port = "127.0.0.1", 5434
    return psycopg2.connect(
        host=host, port=port,
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def coverage(conn, day):
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM market_data WHERE trade_date=%s", (day,))
    n = int(cur.fetchone()[0])
    cur.execute(
        "SELECT count(*) FROM market_data WHERE trade_date < %s "
        "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 21", (day,))
    base = [int(r[0]) for r in cur.fetchall()]
    return n, base


def missing_universe(conn, day, limit=0):
    """당일 행이 없는 종목(최근 30일 내 거래 이력 있음)을 유동성 내림차순으로."""
    cur = conn.cursor()
    cur.execute(
        """
        WITH liq AS (
          SELECT stock_code, AVG(trading_value) tv
          FROM market_data
          WHERE trade_date < %(d)s AND trade_date >= %(d)s::date - 40
          GROUP BY 1)
        SELECT s.stock_code, COALESCE(s.market, 'KOSPI'), COALESCE(liq.tv, 0) tv
        FROM stocks s
        JOIN market_data m ON m.stock_code = s.stock_code
        LEFT JOIN liq ON liq.stock_code = s.stock_code
        WHERE m.trade_date < %(d)s
          AND NOT EXISTS (SELECT 1 FROM market_data p
                          WHERE p.stock_code = s.stock_code AND p.trade_date = %(d)s)
        GROUP BY s.stock_code, s.market, liq.tv
        ORDER BY tv DESC, s.stock_code
        """, {"d": day})
    rows = cur.fetchall()
    if limit:
        rows = rows[: int(limit)]
    return [(c, m) for c, m, _ in rows]


def main(argv=None):
    ap = argparse.ArgumentParser(description="하루치 일봉 결번 종목만 KIS 로 채운다")
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--limit", type=int, default=0, help="이번 실행에서 채울 종목 수(0=전부)")
    ap.add_argument("--ratio", type=float, default=0.6, help="정상일 중위값 대비 목표 비율")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    day = a.date if "-" in a.date else f"{a.date[:4]}-{a.date[4:6]}-{a.date[6:]}"
    if day > date.today().isoformat():
        print(f"미래 날짜 거부: {day}")
        return 2

    conn = _connect()
    try:
        n, base = coverage(conn, day)
        vals = sorted(b for b in base if b > 0)
        median = vals[len(vals) // 2] if vals else 0
        target = int(median * a.ratio) if median else 0
        print(f"[gapfill] {day}: 적재 {n}종목 / 기준선 중위 {median} / 목표 {target}")
        if target and n >= target:
            print("목표 도달 — 채울 필요 없음")
            return 0
        todo = missing_universe(conn, day, a.limit)
    finally:
        conn.close()

    print(f"[gapfill] 채울 종목 {len(todo)}개 (유동성 순)")
    if not todo:
        print("빈 종목 없음 — 부분 적재 아님")
        return 0 if (target and n >= target) else 3

    uni = os.path.join(PROJ, "data", "kis", f"gapfill_universe_{day.replace('-', '')}.json")
    os.makedirs(os.path.dirname(uni), exist_ok=True)
    payload = {c: m for c, m in todo}
    if not a.dry_run:
        with open(uni, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        print(f"[gapfill] 유니버스 파일 {uni} ({len(payload)}종목)")
    else:
        print(f"[dry-run] 첫 10종목: {list(payload)[:10]}")
        return 0

    cmd = ["/usr/bin/python3", "-m", "kis_app.main", "--job", "daily",
           "--date", day.replace("-", ""), "--universe-file", uni]
    env = dict(os.environ)
    env.update(POSTGRES_HOST="127.0.0.1", POSTGRES_PORT="5434", PROJ_DIR=PROJ)
    print(f"[gapfill] 실행: {' '.join(cmd)}")
    rc = subprocess.call(cmd, cwd=KIS_DIR, env=env)

    conn = _connect()
    try:
        n2, _base = coverage(conn, day)
    finally:
        conn.close()
    print(f"[gapfill] 완료 {datetime.now():%H:%M:%S}: 적재 {n} → {n2} (목표 {target}) rc={rc}")
    if rc != 0:
        return rc
    return 0 if (target and n2 >= target) else 3


if __name__ == "__main__":
    sys.exit(main())
