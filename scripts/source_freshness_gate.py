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

  ⚠ 기준 자체의 신선도도 잰다(2026-11-09 실측 사고): 이 게이트는 **상대(기준 대비)** 판정이라
  `market_data` 까지 함께 멈추면 전 원천이 lag 0 으로 '정상'이 된다. 실제로 호스트가 10-11~11-09
  꺼져 있는 동안 모든 테이블이 2026-10-08 에 멈췄는데 게이트는 "전 원천 정상(지연 <2거래일,
  기준 2026-10-08)" 이라고 답했다 — 22거래일 공백을 0 으로 보고한 것이다. 이제 기준 거래일의
  **달력 지연**(기대 마지막 거래일 대비)을 함께 계산해 `reference.verdict` 로 내고, WARN 이상이면
  요약줄 맨 앞에 `⛔` 로 표시한다(임계값은 원천과 동일).

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
from datetime import date, datetime, timedelta, timezone

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
    # 2026-11-13 추가: 모델 패널이 sns_sentiment/attention/momentum 을 이 테이블에서 읽는다.
    # 왜 필요했나(실측): max(trade_date) 기준으로는 '정상'인데 **수집량이 붕괴**해 있었다 —
    # sns_posts 일평균 5,025건(2026-09) → 0건(2026-10) → 531건(2026-11, 종목 308→15).
    # 최신 날짜만 보는 게이트는 이런 '조용한 수집 중단'을 못 잡는다 → VOLUME 검사를 함께 건다.
    ("sns_post_features", "trade_date"),
]
REFERENCE = ("market_data", "trade_date")
WARN_LAG = 2
CRITICAL_LAG = 5
# 수집량 붕괴(VOLUME) 검사 — 최근 7일 일평균이 직전 60일 중앙 일평균의 이 비율 미만이면 경고.
# 기준선이 희소한 원천(중앙 일평균 < 20행)은 검사하지 않는다(이벤트성 테이블 오탐 방지).
VOLUME_MIN_RATIO = 0.25
VOLUME_MIN_BASELINE = 20
VOLUME_RECENT_DAYS = 7
VOLUME_BASELINE_DAYS = 60


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


def volume_check(table: str, col: str, mx: str) -> dict:
    """수집량 붕괴 검사 — 최근 7일 일평균 vs 직전 60일 중앙 일평균.

    왜(2026-11-13 실측): sns_posts 는 max(posted_at)=2026-11-13 로 '신선'했지만 실제로는
    2026-10 전체 0건·11월 일평균 531건(9월 5,025건)으로 **수집이 사실상 멈춰** 있었다.
    날짜 지연만 보는 게이트는 이 상태를 all-green 으로 보고한다.
    """
    try:
        q = (
            "WITH b AS ("
            f" SELECT ({col})::date d, count(*) n FROM {table}"
            f" WHERE ({col})::date >= DATE '{mx}' - {VOLUME_BASELINE_DAYS + VOLUME_RECENT_DAYS}"
            f"   AND ({col})::date <  DATE '{mx}' - {VOLUME_RECENT_DAYS} GROUP BY 1),"
            " r AS ("
            f" SELECT count(*) n FROM {table}"
            f" WHERE ({col})::date > DATE '{mx}' - {VOLUME_RECENT_DAYS})"
            " SELECT coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY n), 0),"
            " coalesce((SELECT n FROM r), 0) FROM b"
        )
        med, recent = psql(q).split("|")
        med = float(med or 0)
        recent = float(recent or 0)
    except Exception as e:                                       # noqa: BLE001
        return {"error": str(e)[:120]}
    per_day = recent / float(VOLUME_RECENT_DAYS)
    ratio = (per_day / med) if med > 0 else None
    flag = bool(med >= VOLUME_MIN_BASELINE and ratio is not None and ratio < VOLUME_MIN_RATIO)
    return {"recent_rows": int(recent), "recent_per_day": round(per_day, 1),
            "baseline_median_daily": round(med, 1),
            "ratio": (round(ratio, 3) if ratio is not None else None), "flag": flag}


HOLIDAYS_PATH = os.path.join(PROJ, "data/krx_holidays.json")


def _holidays() -> set:
    try:
        with open(HOLIDAYS_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:                                            # noqa: BLE001
        return set()


def reference_lag(ref_iso: str, today: date | None = None) -> int:
    """기준 거래일(ref_iso) 이후 **오늘까지 지나간 거래일 수**(주말·휴장 제외).

    원천 lag 은 '기준 대비'라 기준이 멈춘 것을 못 본다. 이 값이 크면 시장 데이터 자체가 낡았다.
    오늘 데이터는 장 마감 후 적재되므로 정상 운영에서 0~1 이다(주말·휴장은 세지 않는다).
    """
    if not ref_iso:
        return -1
    ref = date.fromisoformat(str(ref_iso)[:10])
    today = today or datetime.now(timezone.utc).astimezone().date()
    if ref >= today:
        return 0
    hol = _holidays()
    n, d = 0, ref + timedelta(days=1)
    while d <= today:
        if d.weekday() < 5 and d.isoformat() not in hol:
            n += 1
        d += timedelta(days=1)
    return n


def probe() -> dict:
    ref = psql(f"SELECT max({REFERENCE[1]})::text FROM {REFERENCE[0]}")
    out: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reference": {"table": REFERENCE[0], "date": ref},
        "sources": {},
        "errors": [],
    }
    if ref:
        rlag = reference_lag(ref)
        out["reference"].update({"lag_trading_days": rlag, "verdict": verdict(rlag)})
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
        vc = volume_check(table, col, mx)
        out["sources"][table]["volume"] = vc
        if vc.get("flag"):
            out["sources"][table]["verdict"] = "VOLUME"
            out.setdefault("volume_flags", []).append(
                f"{table} 최근7일 {vc['recent_per_day']}/일 = 기준선 {vc['baseline_median_daily']}/일"
                f"의 ×{vc['ratio']}")
    scored = [(t, v["lag_trading_days"], v["verdict"])
              for t, v in out["sources"].items() if v.get("lag_trading_days") is not None]
    out["worst"] = (max(scored, key=lambda x: x[1]) if scored else None)
    return out


def summary_line(res: dict) -> str:
    worst = res.get("worst")
    ref = res["reference"]["date"]
    rlag = res["reference"].get("lag_trading_days")
    if rlag is not None and rlag >= WARN_LAG:
        rest = ""
        if worst and worst[1] >= WARN_LAG:
            rest = " · " + " · ".join(
                f"{t} {v['lag_trading_days']}거래일({v['verdict']})"
                for t, v in sorted(res["sources"].items(), key=lambda kv: -(kv[1].get("lag_trading_days") or 0))
                if (v.get("lag_trading_days") or 0) >= WARN_LAG)[:200]
        return (f"⛔ 기준 원천 {REFERENCE[0]} 자체가 {rlag}거래일 낡음"
                f"({res['reference'].get('verdict')}, 최신 {ref}) — 원천별 lag 은 전부 0 으로 보인다{rest}")
    if not worst:
        return f"원천 신선도: 측정 불가(기준 {ref})"
    if worst[1] < WARN_LAG:
        vf = res.get("volume_flags") or []
        if vf:
            return (f"⚠ 원천 신선도: 날짜는 정상(지연 <{WARN_LAG}거래일, 기준 {ref})이지만 **수집량 붕괴** — "
                    + " · ".join(vf[:2]))
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
