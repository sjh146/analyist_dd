#!/usr/bin/env python3
"""원천 신선도 게이트 — **배선된** 피처 원천의 정지를 틱마다 잡는다.

WHY (2026-10-11 실측): `supply_market_features`·`financial_ratio_features` 가 2026-09-29 에
멈춰 09-30~10-08 **6거래일이 결손**인데 아무도 몰랐다(그 사이 다른 원천은 10-08 까지 정상).
구동기의 "미배선 원천 스윕"(source_coverage_sweep)은 이름 그대로 **미배선** 원천만 채점하고,
이미 배선된 원천(=모델 입력)의 신선도는 어떤 틱도 보지 않았다 → 12일간 조용한 정지.
공백의 대가는 조용하다: 서빙 피처가 낡은 값을 as-of 로 물고 모델 입력이 조용히 바뀐다.

이 게이트가 하는 일(감지 전용, 쓰기 없음):
  기준 = `market_data` 의 최신 거래일. 원천별 max(날짜)를 읽어 **시장 거래일 기준 지연**을 센다.
  lag ≤1 OK · 2~4 WARN · ≥5 CRITICAL. 결과를 JSON 으로 남기고 한 줄로 요약한다.

재현: python3 scripts/source_freshness_gate.py            (사람/틱)
      python3 scripts/source_freshness_gate.py --json-out <경로>
자체점검: python3 scripts/_source_freshness_test.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(PROJ, "data/reports/source_freshness.json")
PG_CONTAINER = os.environ.get("FRESHNESS_PG_CONTAINER", "stock_postgres")
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")

# (테이블, 날짜 컬럼) — 배선된(=모델이 읽는) 피처 원천만. 기준은 market_data.trade_date.
# ⚠ financial_statements 는 **일부러 뺐다**: report_date 는 분기 말일(2026-06-30)이라 거래일
#   기준 지연이 67거래일로 찍힌다 — 정지가 아니라 회계 주기의 정상값이다(첫 실행에서 오탐 실측
#   2026-10-11). 이 원천의 as-of 신선도는 rcept_dt 격자(patch_panel_fin_rcept)로 따로 본다.
SOURCES = [
    ("supply_market_features", "trade_date"),
    ("financial_ratio_features", "trade_date"),
    ("macro_features", "trade_date"),
    ("foreign_institutional", "trade_date"),
    ("event_features", "trade_date"),
    ("krx_short_selling", "trade_date"),
    ("disclosures", "rcept_dt"),
]
REFERENCE = ("market_data", "trade_date")
WARN_LAG = 2
CRITICAL_LAG = 5


def psql(sql: str, timeout: int = 60) -> str:
    cmd = ["docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB, "-tAc", sql]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:200])
    return r.stdout.strip()


def verdict(lag: int) -> str:
    if lag >= CRITICAL_LAG:
        return "CRITICAL"
    if lag >= WARN_LAG:
        return "WARN"
    return "OK"


def probe() -> dict:
    ref = psql(f"SELECT max({REFERENCE[1]})::text FROM {REFERENCE[0]}")
    out: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reference": {"table": REFERENCE[0], "date": ref},
        "sources": {},
        "errors": [],
    }
    if not ref:
        out["errors"].append("기준 거래일 없음(market_data 비었음)")
        out["worst"] = None
        return out
    for table, col in SOURCES:
        try:
            mx = psql(f"SELECT max({col})::text FROM {table}")
        except Exception as e:                                   # noqa: BLE001
            out["sources"][table] = {"column": col, "max_date": None, "error": str(e)[:120]}
            out["errors"].append(f"{table}: {str(e)[:80]}")
            continue
        if not mx:
            out["sources"][table] = {"column": col, "max_date": None,
                                     "lag_trading_days": None, "verdict": "EMPTY"}
            continue
        lag = int(psql(f"SELECT count(DISTINCT trade_date) FROM market_data"
                       f" WHERE trade_date > DATE '{mx}'") or 0)
        out["sources"][table] = {"column": col, "max_date": mx, "lag_trading_days": lag,
                                 "verdict": verdict(lag)}
    scored = [(t, v["lag_trading_days"], v["verdict"])
              for t, v in out["sources"].items() if v.get("lag_trading_days") is not None]
    out["worst"] = (max(scored, key=lambda x: x[1]) if scored else None)
    return out


def summary_line(res: dict) -> str:
    worst = res.get("worst")
    ref = res["reference"]["date"]
    if not worst:
        return f"원천 신선도: 측정 불가(기준 {ref})"
    if worst[1] < WARN_LAG:
        return f"원천 신선도: 전 원천 정상(지연 <{WARN_LAG}거래일, 기준 {ref})"
    bad = [(t, v["lag_trading_days"], v["verdict"]) for t, v in res["sources"].items()
           if v.get("lag_trading_days") is not None and v["lag_trading_days"] >= WARN_LAG]
    bad.sort(key=lambda x: -x[1])
    detail = " · ".join(f"{t} {lag}거래일({vd})" for t, lag, vd in bad[:4])
    return f"⚠ 원천 신선도 {worst[2]}: {detail} (기준 {ref}, 최신 {worst[0]} = {res['sources'][worst[0]]['max_date']})"


def main() -> int:
    ap = argparse.ArgumentParser(description="배선된 피처 원천 신선도 게이트(감지 전용)")
    ap.add_argument("--json-out", default=EVID)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    res = probe()
    os.makedirs(os.path.dirname(a.json_out), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    if not a.quiet:
        print(summary_line(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
