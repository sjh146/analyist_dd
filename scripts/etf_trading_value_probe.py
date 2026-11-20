#!/usr/bin/env python3
"""ETF/ETN trading_value 결측 프로브 (읽기 전용 — DB 를 고치지 않는다).

WHY (실측 2026-11-20 22:5x, R45):
  market_data 의 **KOSPI ETF 행이 trading_value NULL 로 적재**된다.
  11-19 실측: volume>0 3,722행 중 1,157행(31.1%)이 trading_value NULL 이고 그 1,157행이
  전부 KOSPI ETF 였다. 같은 날 거래대금 합계가 적재값 17.99조 vs close×volume 28.21조
  → **36% 과소집계**. 시장패턴 원장(trade_value_sum)과 유동성 필터(feature_ic_probe
  min_trading_value)가 이 컬럼을 읽으므로 '시장 거래대금이 반토막'으로 오독된다.
  11-13·11-17·11-18 은 0건(다른 경로가 채운 날), 11-05~11-19 대부분 날짜가 850~1,161건.

  계약 #2(NaN≠0): 결측을 0 으로 채우지 않는다. 이 프로브는 **세기만** 한다.

출력: 마지막 줄이 결함 행수(정수) — researcher_cycle 의 check_target 이 그 수치로 판정한다.
사용: /usr/bin/python3 scripts/etf_trading_value_probe.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOTENV = os.path.join(PROJ, ".env")


def _load_dotenv(path: str) -> dict:
    """`.env` 는 **컨테이너 좌표**를 담고 있다(postgres:5432) — 호스트에서 그대로 쓰면
    이름해석 실패로 죽는다(실측 2026-09-28 R16). 호스트에서는 127.0.0.1:호스트포트로 복원한다.
    """
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def pg_params() -> dict:
    env = _load_dotenv(DOTENV)
    host = os.environ.get("POSTGRES_HOST") or env.get("POSTGRES_HOST") or "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT") or env.get("POSTGRES_PORT") or 5434)
    in_docker = os.path.exists("/.dockerenv")
    if not in_docker and (host == "postgres" or port == 5432):
        host, port = "127.0.0.1", int(env.get("POSTGRES_HOST_PORT") or 5434)
    return dict(
        host=host,
        port=port,
        user=os.environ.get("POSTGRES_USER") or env.get("POSTGRES_USER") or "stock_user",
        password=os.environ.get("POSTGRES_PASSWORD") or env.get("POSTGRES_PASSWORD") or "",
        dbname=os.environ.get("POSTGRES_DB") or env.get("POSTGRES_DB") or "stock_trading",
    )


QUERY = """
WITH d AS (SELECT max(trade_date) AS d FROM market_data WHERE volume > 0)
SELECT m.trade_date,
       count(*) FILTER (WHERE m.volume > 0)                                   AS rows_vol,
       count(*) FILTER (WHERE m.volume > 0 AND m.trading_value IS NULL)       AS null_tv,
       count(*) FILTER (WHERE m.volume > 0 AND m.trading_value IS NULL
                          AND coalesce(s.instrument_type, '') IN ('ETF','ETN')) AS null_tv_etf,
       coalesce(sum(m.trading_value) FILTER (WHERE m.volume > 0), 0) / 1e12    AS tv_sum_jo,
       coalesce(sum(m.close_price * m.volume) FILTER (WHERE m.volume > 0), 0) / 1e12 AS close_vol_jo
FROM market_data m
JOIN d ON m.trade_date = d.d
LEFT JOIN stocks s ON s.stock_code = m.stock_code
GROUP BY 1
"""

QUERY_DATE = QUERY.replace("FROM market_data WHERE volume > 0) AS d", "FROM market_data WHERE volume > 0) AS d")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="trading_value 결측 프로브(읽기 전용)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (기본 = 최신 거래일)")
    args = ap.parse_args(argv)
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 없음 — /usr/bin/python3 로 실행하라", file=sys.stderr)
        return 2
    try:
        conn = psycopg2.connect(**pg_params())
    except Exception as exc:  # noqa: BLE001
        print(f"DB 연결 실패: {exc}", file=sys.stderr)
        return 2
    conn.set_session(autocommit=True)
    try:
        cur = conn.cursor()
        if args.date:
            d = date.fromisoformat(args.date)
            cur.execute(
                """
                SELECT count(*) FILTER (WHERE volume > 0),
                       count(*) FILTER (WHERE volume > 0 AND trading_value IS NULL),
                       coalesce(sum(trading_value) FILTER (WHERE volume > 0), 0) / 1e12,
                       coalesce(sum(close_price * volume) FILTER (WHERE volume > 0), 0) / 1e12
                FROM market_data WHERE trade_date = %s
                """,
                (d,),
            )
            rows_vol, null_tv, tv, cv = cur.fetchone()
            print(f"[probe] date={d} rows_vol={rows_vol} null_tv={null_tv} "
                  f"tv={float(tv or 0):.2f}조 close*vol={float(cv or 0):.2f}조 "
                  f"과소={0.0 if not cv else 100 * (1 - float(tv or 0) / float(cv)):.1f}%")
        else:
            cur.execute(QUERY)
            row = cur.fetchone()
            if not row:
                print("[probe] market_data 비어 있음 — 판정 불가", file=sys.stderr)
                print(999)
                return 0
            td, rows_vol, null_tv, null_tv_etf, tv, cv = row
            tv, cv = float(tv or 0), float(cv or 0)
            short = 0.0 if cv <= 0 else 100 * (1 - tv / cv)
            print(f"[probe] date={td} rows_vol={rows_vol} null_tv={null_tv} "
                  f"(ETF/ETN {null_tv_etf}) tv={tv:.2f}조 close*vol={cv:.2f}조 과소={short:.1f}%")
            print(f"[probe] NULL 비율 = {0.0 if not rows_vol else 100 * null_tv / rows_vol:.1f}% "
                  f"| 계약 #2: 결측을 0 으로 채우지 말고 원천에서 채운다")
        print(int(null_tv or 0))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
