#!/usr/bin/env python3
"""시가총액 + 밸류에이션 비율(PER/PBR/ROE) 채우기 — KRX OpenAPI(무료) + DART 재무값.

WHY
  `services/xgboost-ml/app/feature_engine/factor_features.py` 의 밸류/퀄리티 피처는
    - `stocks.market_cap`      (value_psr, value_ev_ebit)
    - `financial_statements.per/pbr/roe` (value_per, value_pbr, quality_roe, per_current, ...)
  를 읽는다. 실측(2026-09-24) 당시 stocks.market_cap 은 **전 행 NULL**, financial_statements 의
  per/pbr/roe 는 **전 행 NULL** 이라 해당 피처가 전부 상수 0 이었다.

무엇을 하나
  1) KRX OpenAPI 일별매매정보(sto/stk_bydd_trd, sto/ksq_bydd_trd)에서 MKTCAP/LIST_SHRS 를
     가져온다 — 날짜 1개 = 2콜(전 종목). 재무제표 report_date 들 + 최신 거래일을 조회해
     `data/krx/market_caps.json` 에 캐시한다.
  2) `stocks.market_cap` 을 최신 거래일 시가총액으로 채운다.
  3) financial_statements 각 행에 대해 (연결/별도 구분 없이 저장된 값 기준)
       - ni_ttm: 연간(12-31)은 그 해 순이익, 반기(06-30)는 ×2, 1분기(03-31)는 ×4, 3분기(09-30)는 ×4/3
       - per = 시가총액 / ni_ttm (양수일 때)
       - pbr = 시가총액 / 자본총계
       - roe = ni_ttm / 자본총계 × 100
     를 기록한다. 시가총액은 **그 종목의 최신 report_date 행만 '최신 거래일 시총'**,
     나머지 과거 행은 그 report_date 시점 시총(룩어헤드 없음)을 쓴다.

사용 (호스트, /usr/bin/python3 = psycopg2/requests 설치본):
  /usr/bin/python3 scripts/refresh_valuation_ratios.py --dry-run
  /usr/bin/python3 scripts/refresh_valuation_ratios.py --write
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

BASE_DIR = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
CACHE_PATH = os.path.join(BASE_DIR, "data", "krx", "market_caps.json")
MARKETS = {"KOSPI": "sto/stk_bydd_trd", "KOSDAQ": "sto/ksq_bydd_trd"}
BASE_CANDIDATES = ["https://data-dbg.krx.co.kr/svc/apis", "https://data.krx.co.kr/svc/apis"]
DELAY = float(os.environ.get("KRX_REQUEST_DELAY", "3.0"))
JITTER = float(os.environ.get("KRX_REQUEST_JITTER", "0.5"))
# 반기/분기 손익 → 연간 환산 계수 (TTM 근사)
ANNUALIZE = {"12-31": 1.0, "06-30": 2.0, "03-31": 4.0, "09-30": 4.0 / 3.0}


def load_env():
    env = {}
    with open(os.path.join(BASE_DIR, ".env")) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


def pg_conn(env):
    port = int(env.get("POSTGRES_HOST_PORT") or env.get("POSTGRES_PORT", 5434))
    last = None
    for host in (env.get("POSTGRES_HOST", "127.0.0.1"), "127.0.0.1", "localhost"):
        try:
            return psycopg2.connect(
                host=host, port=port,
                user=env.get("POSTGRES_USER", "stock_user"),
                password=env.get("POSTGRES_PASSWORD", ""),
                dbname=env.get("POSTGRES_DB", "stock_trading"),
                connect_timeout=5,
            )
        except psycopg2.OperationalError as e:  # 도커 내부 호스트명은 호스트에서 안 풀린다
            last = e
    raise last


def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def resolve_base(key, state):
    if state.get("base_url"):
        return state["base_url"]
    for base in BASE_CANDIDATES:
        try:
            rows = fetch_day(base, key, MARKETS["KOSPI"], "20260922")
            if rows is not None:
                state["base_url"] = base
                return base
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("KRX OpenAPI 호스트를 찾지 못했습니다")


def fetch_day(base, key, market_path, basdd):
    url = f"{base}/{market_path}?basDd={basdd}"
    req = urllib.request.Request(url, headers={"AUTH_KEY": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read().decode("utf-8", "replace"), e.code
    if code >= 400:
        raise RuntimeError(f"HTTP {code} {raw[:120]}")
    data = json.loads(raw)
    rows = data.get("OutBlock_1")
    if rows is None:
        raise RuntimeError(f"OutBlock_1 없음: {raw[:120]}")
    return rows


def fetch_caps(key, state, dates):
    """{date_str: {stock_code: market_cap}} — KRX OpenAPI (날짜당 마켓 2콜)."""
    base = resolve_base(key, state)
    out = {}
    for d in dates:
        basdd = d.replace("-", "")
        caps = {}
        for mkt, path in MARKETS.items():
            rows = []
            for attempt in range(2):
                try:
                    rows = fetch_day(base, key, path, basdd)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 1:
                        print(f"  {basdd} {mkt} 실패: {e}", file=sys.stderr)
                        rows = []
                    time.sleep(DELAY)
            for r in rows:
                code = str(r.get("ISU_CD") or "").strip()
                cap = num(r.get("MKTCAP"))
                if code and cap:
                    caps[code] = cap
            time.sleep(DELAY + random.uniform(0, JITTER))
        if caps:
            out[d] = caps
            print(f"  {d}: {len(caps)}종목 시총")
        else:
            print(f"  {d}: 시총 없음(휴장/미공표) — 건너뜀")
    return out


def latest_cap_date(caps, target):
    """target(YYYY-MM-DD) 이하에서 가장 가까운 시총 날짜."""
    cand = [d for d in caps if d <= target]
    return max(cand) if cand else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true", help="DB 반영")
    ap.add_argument("--refresh-cache", action="store_true", help="KRX 재조회 후 캐시 갱신")
    ap.add_argument("--extra-dates", default="", help="추가 조회 날짜(콤마, YYYY-MM-DD)")
    args = ap.parse_args()

    env = load_env()
    key = env.get("KRX_API_KEY", "").strip()
    conn = pg_conn(env)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT report_date::text FROM financial_statements ORDER BY 1")
    rdates = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT max(trade_date)::text FROM market_data")
    last_md = cur.fetchone()[0]
    cur.execute("SELECT max(trade_date)::text FROM market_data WHERE trade_date <= %s",
                (datetime.now().date().isoformat(),))
    dates = sorted(set(rdates) | {d for d in (last_md,) if d})
    if last_md:
        # 최신 거래일 시총이 아직 공표 전일 수 있으니 하루 전도 후보에 넣는다
        dates = sorted(set(dates) | {(date.fromisoformat(last_md) - timedelta(days=1)).isoformat()})
    dates += [d.strip() for d in args.extra_dates.split(",") if d.strip()]
    dates = sorted(set(dates))
    print(f"시총 조회 대상 날짜 {len(dates)}개: {dates} (콜 {len(dates)*2}회 예상)")

    caps = {}
    if os.path.exists(CACHE_PATH) and not args.refresh_cache:
        with open(CACHE_PATH) as f:
            caps = json.load(f)
    need = [d for d in dates if d not in caps]
    if args.dry_run:
        print(f"[dry-run] 캐시 보유 {len(caps)}일 / 추가 필요 {need}")
        conn.close()
        return
    if need:
        if not key:
            raise SystemExit("KRX_API_KEY 없음")
        state_path = os.path.join(BASE_DIR, "data", "krx", "rate_state.json")
        state = {}
        if os.path.exists(state_path):
            with open(state_path) as f:
                state = json.load(f)
        print(f"KRX 조회: {need}")
        new = fetch_caps(key, state, need)
        caps.update(new)
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(caps, f)
        os.replace(tmp, CACHE_PATH)
        try:  # data/krx/rate_state.json 은 krx_daily.py(root 실행) 소유일 수 있다 → 실패 무시
            with open(state_path, "w") as f:
                json.dump(state, f)
        except OSError as e:
            print(f"  (rate_state 저장 생략: {e})", file=sys.stderr)
        print(f"캐시 저장: {CACHE_PATH} ({len(caps)}일)")

    if not args.write:
        print("[캐시만] --write 없이는 DB 반영 안 함")
        conn.close()
        return

    # 1) stocks.market_cap ← 최신 시총
    cur_date = latest_cap_date(caps, datetime.now().date().isoformat())
    latest = caps.get(cur_date, {})
    n_cap = 0
    for code, cap in latest.items():
        cur.execute("UPDATE stocks SET market_cap=%s, updated_at=CURRENT_TIMESTAMP "
                    "WHERE stock_code=%s AND (market_cap IS NULL OR market_cap <> %s)",
                    (int(cap), code, int(cap)))
        n_cap += cur.rowcount
    conn.commit()
    print(f"stocks.market_cap 갱신: {n_cap}행 (기준일 {cur_date}, 대상 {len(latest)}종목)")

    # 2) financial_statements.per/pbr/roe
    cur.execute("SELECT id, stock_code, report_date::text, net_income, total_equity "
                "FROM financial_statements")
    rows = cur.fetchall()
    cur.execute("SELECT stock_code, max(report_date)::text FROM financial_statements GROUP BY 1")
    latest_rd = dict(cur.fetchall())

    upd, skipped = [], 0
    for rid, code, rd, ni, equity in rows:
        ni = float(ni) if ni is not None else None
        equity = float(equity) if equity is not None else None
        suffix = rd[5:]
        factor = ANNUALIZE.get(suffix)
        if factor is None:
            skipped += 1
            continue
        use_date = cur_date if latest_rd.get(code) == rd else latest_cap_date(caps, rd)
        cap = (caps.get(use_date) or {}).get(code) if use_date else None
        per = pbr = roe = None
        if cap and equity and equity > 0:
            pbr = round(cap / equity, 4)
            if ni is not None:
                ni_ttm = ni * factor
                roe = round(ni_ttm / equity * 100.0, 4)
                if ni_ttm > 0:
                    per = round(cap / ni_ttm, 4)
        upd.append((per, pbr, roe, rid))

    cur.executemany("UPDATE financial_statements SET per=%s, pbr=%s, roe=%s WHERE id=%s", upd)
    conn.commit()
    filled = sum(1 for u in upd if u[2] is not None)
    print(f"financial_statements per/pbr/roe 갱신: {len(upd)}행 (roe 채움 {filled}, "
          f"스킵 {skipped})")
    conn.close()


if __name__ == "__main__":
    main()
