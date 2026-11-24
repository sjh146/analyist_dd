#!/usr/bin/env python3
"""DART 대량보유(5%룰) 보고 → 기관 보유비율 추정 적재.

WHY(2026-11-24): `ownership.institution_ownership_pct` 가 전부 NULL 이고 KRX/KIS 에
기관 보유비율 원천이 없다. DART `majorstock.json` 은 5% 이상 보유 보고자를 비율과 함께
돌려주므로(실측: 삼성전자 39건, 삼성물산 20.07%), 그중 **기관성 보고자**의 최신 비율을
합산해 기관 보유비율 추정치로 쓴다.

한계: 5% 미만 기관 지분은 잡히지 않으므로 **하한 추정**이다(문서에 명시).

사용:
  python3 scripts/dart_major_holders.py --limit 30
  python3 scripts/dart_major_holders.py            # 전체 유니버스
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import psycopg2

API = "https://opendart.fss.or.kr/api/majorstock.json"
INST = re.compile(r"자산운용|투자|연금|보험|은행|증권|캐피탈|운용|자문|신탁|"
                  r"Fund|Capital|Investment|Asset|Trust|Pension|Insurance", re.I)


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def ensure_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS major_holders (
            stock_code TEXT NOT NULL,
            rcept_dt DATE NOT NULL,
            reporter TEXT NOT NULL,
            stake_pct NUMERIC,
            stake_qty BIGINT,
            report_tp TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (stock_code, rcept_dt, reporter)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS institutional_ownership (
            stock_code TEXT NOT NULL,
            as_of DATE NOT NULL,
            inst_pct NUMERIC,
            inst_reporters INT,
            created_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (stock_code, as_of)
        )
    """)


def fetch(corp_code: str, key: str, timeout: float = 20.0):
    url = API + "?" + urllib.parse.urlencode({"crtfc_key": key, "corp_code": corp_code})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler())
    with opener.open(url, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.08)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("DART_API_KEY 없음", file=sys.stderr)
        return 2

    conn = connect()
    cur = conn.cursor()
    ensure_tables(cur)
    conn.commit()

    q = ("SELECT stock_code, max(corp_code) FROM disclosures "
         "WHERE corp_code IS NOT NULL AND stock_code IS NOT NULL GROUP BY 1 ORDER BY 1")
    if args.limit:
        q += " LIMIT {0}".format(int(args.limit))
    cur.execute(q)
    pairs = cur.fetchall()
    print("대상 종목", len(pairs))

    ok = fail = 0
    for stock_code, corp_code in pairs:
        try:
            data = fetch(corp_code, key)
        except Exception as exc:
            print(stock_code, "fetch 실패", str(exc)[:80])
            fail += 1
            continue
        rows = data.get("list") or []
        if data.get("status") != "000" or not rows:
            fail += 1
            time.sleep(args.sleep)
            continue
        latest: dict[str, tuple] = {}
        for row in rows:
            reporter = (row.get("repror") or "").strip()
            rcept = (row.get("rcept_dt") or "").strip()
            if not reporter or not rcept:
                continue
            try:
                pct = float((row.get("stkrt") or "0").replace(",", ""))
            except ValueError as exc:
                print(stock_code, "지분율 파싱 실패:", row.get("stkrt"), type(exc).__name__)
                continue
            prev = latest.get(reporter)
            if prev is None or rcept > prev[0]:
                qty_raw = (row.get("stkqy") or "0").replace(",", "")
                qty = int(qty_raw) if qty_raw.isdigit() else 0
                latest[reporter] = (rcept, pct, qty, row.get("report_tp") or "")
        if not args.dry_run and latest:
            cur.executemany(
                "INSERT INTO major_holders (stock_code, rcept_dt, reporter, stake_pct, stake_qty, report_tp) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (stock_code, rcept_dt, reporter) DO NOTHING",
                [(stock_code, v[0], rep, v[1], v[2], v[3]) for rep, v in latest.items()])
            inst = {rep: v for rep, v in latest.items() if INST.search(rep)}
            if inst:
                as_of = max(v[0] for v in inst.values())
                pct = sum(v[1] for v in inst.values())
                cur.execute(
                    "INSERT INTO institutional_ownership (stock_code, as_of, inst_pct, inst_reporters) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (stock_code, as_of) DO UPDATE "
                    "SET inst_pct = EXCLUDED.inst_pct, inst_reporters = EXCLUDED.inst_reporters",
                    (stock_code, as_of, round(pct, 4), len(inst)))
        ok += 1
        time.sleep(args.sleep)
        if ok % 50 == 0:
            conn.commit()
            print("진행", ok, "/", len(pairs))

    if not args.dry_run:
        conn.commit()
    cur.execute("SELECT count(*), count(inst_pct) FROM institutional_ownership")
    total, with_pct = cur.fetchone()
    print("완료: ok={0} fail={1} | institutional_ownership {2}행".format(ok, fail, total))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
