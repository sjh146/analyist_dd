#!/usr/bin/env python3
"""stocks.sector 채우기 — Naver 업종분류 API (KIS 호출 0회).

WHY (R17 잔여분, 2026-11-21 실측):
  stocks 4,353행 중 sector 가 비어 있는 행이 **2,809**(65%) 이고, 채워진 값은 'ETF'/'ETN'
  뿐이다(업종이 아니라 상품 종류). R17 의 check 3조건 중 공매도 커버리지(2,627종목)와
  SNS 신선도(11-20)는 이미 해소됐고 **섹터 결측만 남았다**.
  KIS 로 채우면 종목당 1콜(bstp_kor_isnm, 실측 '전기·전자') = 2,809콜 ≈ 2.7시간이라
  일일 한도 미확인 상태(R20)에서 수급 회전과 쿼터를 다툰다.

경로(실측 2026-11-21):
  GET https://m.stock.naver.com/api/stocks/industry            → groups[{no,name,totalCount}] (페이지 20개)
  GET https://m.stock.naver.com/api/stocks/industry/<no>?page=N&pageSize=100 → stocks[{itemCode,...}]
  전체 업종 ~100 × 종목 4,353 → **약 50콜**. 키 불필요, net_guard('naver') 경유.

동작:
  - 6자리 코드만 대상. `--overwrite` 없으면 sector 가 NULL/'' 인 행만 채운다
    (기존 'ETF'/'ETN' 표기는 건드리지 않는다).
  - 업종에 안 잡히는 종목(KONEX·스팩·신규상장 등)은 세어서 보고한다.
  - `--dry-run` 은 조회·매핑까지만 하고 DB 를 쓰지 않는다.

사용:
  python3 scripts/fill_sector_naver.py --dry-run
  python3 scripts/fill_sector_naver.py
"""
import argparse
import json
import os
import random
import sys
import time
from datetime import datetime

import psycopg2
import requests

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, "scripts"))

try:
    import net_guard  # noqa: E402
except Exception:  # noqa: BLE001
    net_guard = None

BASE = "https://m.stock.naver.com/api/stocks/industry"
DELAY = float(os.environ.get("NAVER_REQUEST_DELAY", "0.5"))
JITTER = float(os.environ.get("NAVER_REQUEST_JITTER", "0.3"))
BUDGET = int(os.environ.get("NAVER_DAILY_BUDGET", "0"))
UA = {"User-Agent": "Mozilla/5.0 (research; analyist_dd)"}
calls = {"n": 0}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get(url, params=None, retries=3):
    g = None
    if net_guard is not None:
        try:
            g = net_guard.guard("naver", delay=DELAY, jitter=JITTER, budget=BUDGET)
        except Exception:  # noqa: BLE001 — 가드는 fail-open
            g = None
    for attempt in range(retries):
        if g is not None:
            try:
                g.acquire()
            except Exception as exc:  # noqa: BLE001 — 정책 중단은 재시도 없이
                raise RuntimeError(f"수집 정책 중단({type(exc).__name__}): {exc}") from exc
        try:
            calls["n"] += 1
            r = requests.get(url, params=params, timeout=25, headers=UA)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 429):
                raise RuntimeError(f"차단 신호 HTTP {r.status_code}")
            log(f"WARN http={r.status_code} {url}")
        except requests.RequestException as exc:
            log(f"WARN {type(exc).__name__}: {exc}")
        time.sleep((2 ** attempt) + random.random())
    return None


def industries():
    """업종 목록 → [{no, name, totalCount}] (페이지네이션)."""
    out, page, size = [], 1, 100
    while True:
        d = get(BASE, {"page": page, "pageSize": size})
        if not d:
            break
        groups = d.get("groups") or []
        out.extend(groups)
        total = int(d.get("totalCount") or 0)
        if not groups or len(out) >= total:
            break
        page += 1
    return out


def industry_stocks(no):
    """업종 1개의 전 종목 코드 목록."""
    codes, page, size = [], 1, 100
    while True:
        d = get(f"{BASE}/{no}", {"page": page, "pageSize": size})
        if not d:
            break
        items = d.get("stocks") or []
        codes.extend(s.get("itemCode") for s in items if s.get("itemCode"))
        total = int((d.get("groupInfo") or {}).get("totalCount") or d.get("totalCount") or 0)
        if not items or len(codes) >= total:
            break
        page += 1
    return [c for c in codes if isinstance(c, str) and len(c) == 6 and c.isdigit()]


def pg():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main():
    ap = argparse.ArgumentParser(description="stocks.sector 채우기(Naver 업종분류)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true", help="기존 sector 값도 덮어쓴다")
    args = ap.parse_args()

    inds = industries()
    log(f"업종 {len(inds)}개 / 호출 {calls['n']}")
    mapping = {}
    for i, g in enumerate(inds, 1):
        no, name = g.get("no"), (g.get("name") or "").strip()
        if not no or not name:
            continue
        codes = industry_stocks(no)
        for c in codes:
            mapping.setdefault(c, name)
        if i % 20 == 0:
            log(f"  진행 {i}/{len(inds)} 업종 · 누적 종목 {len(mapping)} · 호출 {calls['n']}")
    log(f"업종→종목 매핑 {len(mapping)}종목 / 총 호출 {calls['n']}")

    conn = pg()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT stock_code, sector FROM stocks")
            rows = cur.fetchall()
            todo = [(c, mapping[c]) for c, s in rows
                    if c in mapping and (args.overwrite or not (s or "").strip())]
            no_map = [c for c, _ in rows if c not in mapping]
            log(f"DB {len(rows)}행 / 채울 대상 {len(todo)} / 업종에 없는 종목 {len(no_map)}")
            if args.dry_run:
                for c, s in todo[:10]:
                    log(f"  예시 {c} → {s}")
                log("dry-run — DB 쓰기 생략")
                return 0
            cur.executemany("UPDATE stocks SET sector=%s WHERE stock_code=%s",
                            [(s, c) for c, s in todo])
            cur.execute("SELECT count(*) FROM stocks WHERE sector IS NULL OR sector=''")
            left = cur.fetchone()[0]
        log(f"적용 {len(todo)}행 / 남은 결측 {left}행")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
