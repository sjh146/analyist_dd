#!/usr/bin/env python3
"""feature_window_guard.py — 모델 입력 피처의 '창 생존' 상시 감시 (XR43).

WHY (실측 2026-11-19, R43): 피처 빌더가 격자 하한(--since)만 로드하면 252거래일
shift 가 전부 NaN 이 되어 momentum_3_12m 이 **창 전체에서 0** 이 된다. 원천은 멀쩡하고
'로드 창'만 좁은 경우라 수집 실패 신호(빈 응답·0행)에는 전혀 걸리지 않는다.
따라서 모델 입력 테이블을 (a) 최근 창의 살아있음(nonzero 비율) 과 (b) 창 시작 이전의
가격 이력 깊이(=lookback 확보) 두 축으로 매번 채점한다.

판정(창 단위, 두 창 모두 '거래일' 기준):
  DEAD      : 이전 창 커버리지 ≥0.02 인데 최근 창 커버리지 == 0  → 조용한 사망
  COLLAPSE  : 이전 창 ≥0.10 이고 최근 창 < 이전 창의 50%
  WEAK      : 최근 창 커버리지 < 0.02 (처음부터 무정보 — 모델 기여 불가)
  OK        : 그 외
이력 깊이: 최근 창 시작일 이전 market_data 고유 거래일수가 요구 lookback(기본 252) 미만이면
  HISTORY_SHORT — 이 상태에서 모멘텀이 0 이면 '수리 대상은 창'이다(원천 결측 아님).

사용:
  python3 scripts/feature_window_guard.py            # 사람용 표
  python3 scripts/feature_window_guard.py --json data/reports/feature_window_guard.json
  python3 scripts/feature_window_guard.py --window 40 --lookback 252 --quiet
종료코드: 0 = DEAD/COLLAPSE/HISTORY_SHORT 없음 · 1 = 하나라도 있음 · 2 = 실행 오류
읽기 전용(SELECT 만). 테이블 스키마·계약을 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import psycopg2

TABLE = "supply_market_features"
META = {"stock_code", "trade_date", "computed_at"}


def _env(path=".env"):
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def connect():
    e = {**_env(), **os.environ}
    return psycopg2.connect(
        host=e.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(e.get("POSTGRES_PORT", "5434")),
        dbname=e.get("POSTGRES_DB", "stock_trading"),
        user=e.get("POSTGRES_USER", "stock_user"),
        password=e.get("POSTGRES_PASSWORD", ""),
    )


def feature_columns(cur):
    cur.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_name=%s order by ordinal_position", (TABLE,))
    return [c for c, t in cur.fetchall()
            if c not in META and t in ("double precision", "real", "numeric", "integer", "bigint")]


def coverage(cur, cols, d_from, d_to_excl):
    """(d_from, d_to_excl) 구간의 피처별 nonzero 비율."""
    sel = ", ".join(
        f"coalesce(sum(case when coalesce({c},0) <> 0 then 1 else 0 end)::float/"
        f"nullif(count(*),0), 0) as {c}" for c in cols)
    cur.execute(
        f"select {sel} from {TABLE} where trade_date >= %s and trade_date < %s",
        (d_from, d_to_excl))
    row = cur.fetchone()
    return {c: float(v or 0.0) for c, v in zip(cols, row)}


def classify(prev, curr):
    if prev >= 0.02 and curr == 0.0:
        return "DEAD"
    if prev >= 0.10 and curr < 0.5 * prev:
        return "COLLAPSE"
    if curr < 0.02:
        return "WEAK"
    return "OK"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=40, help="창 크기(거래일)")
    ap.add_argument("--lookback", type=int, default=252, help="모멘텀 요구 선행 거래일")
    ap.add_argument("--json", default="", help="결과 JSON 경로")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    cur.execute(f"select distinct trade_date from {TABLE} order by trade_date desc limit %s",
                (a.window * 2,))
    dates = [r[0] for r in cur.fetchall()]
    if len(dates) < a.window + 5:
        print(f"[guard] 표본 부족: 거래일 {len(dates)}개", file=sys.stderr)
        return 2
    curr_lo, curr_hi = dates[a.window - 1], dates[0]
    prev_lo, prev_hi = dates[min(len(dates) - 1, a.window * 2 - 1)], dates[a.window]

    cols = feature_columns(cur)
    if not cols:
        print("[guard] 피처 컬럼 0개", file=sys.stderr)
        return 2
    curr = coverage(cur, cols, curr_lo, curr_hi)
    prev = coverage(cur, cols, prev_lo, curr_lo)  # 상한 제외 → 현재 창과 겹치지 않는다
    rows = [dict(feature=c, prev=round(prev[c], 4), curr=round(curr[c], 4),
                 status=classify(prev[c], curr[c])) for c in cols]

    # 창 시작 이전 가격 이력 깊이
    cur.execute("select count(distinct trade_date) from market_data where trade_date < %s", (curr_lo,))
    hist_days = int(cur.fetchone()[0] or 0)
    history_ok = hist_days >= a.lookback

    bad = [r for r in rows if r["status"] in ("DEAD", "COLLAPSE")]
    dead = [r for r in rows if r["status"] == "DEAD"]
    weak = [r for r in rows if r["status"] == "WEAK"]
    mom = next((r for r in rows if r["feature"] == "momentum_3_12m"), None)

    verdict = "PASS" if (not bad and history_ok) else "FAIL"
    out = dict(
        verdict=verdict,
        table=TABLE,
        window_days=a.window,
        curr_window=[str(curr_lo), str(curr_hi)],
        prev_window=[str(prev_lo), str(prev_hi)],
        n_features=len(cols),
        history_days_before_window=hist_days,
        history_required=a.lookback,
        history_ok=history_ok,
        dead=[r["feature"] for r in dead],
        collapse=[r["feature"] for r in bad if r["status"] == "COLLAPSE"],
        weak_count=len(weak),
        momentum_3_12m=mom,
        rows=sorted(rows, key=lambda r: r["curr"]),
        computed_at=datetime.now(timezone.utc).isoformat(),
    )

    if not a.quiet:
        print(f"[guard] {verdict} · 창 {curr_lo}~{curr_hi} vs 이전 {prev_lo}~{prev_hi} · "
              f"피처 {len(cols)} · 선행이력 {hist_days}/{a.lookback}거래일")
        for r in out["rows"]:
            if r["status"] != "OK":
                print(f"  {r['status']:<9} {r['feature']:<28} prev {r['prev']:.4f} → curr {r['curr']:.4f}")
        print(f"  DEAD {len(dead)} · COLLAPSE {len(out['collapse'])} · WEAK {len(weak)} · "
              f"모멘텀 {mom['curr'] if mom else 'n/a'}")
    if a.json:
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        if not a.quiet:
            print(f"  → {a.json}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
