#!/usr/bin/env python3
"""이벤트/공시 축 실효 커버리지 프로브 (2026-10-02, CG67 사전조사).

패널 유니버스(49종목) 기준으로 `disclosures`·`event_features` 가 실제로 얼마나
덮는지, 그리고 패널의 `disclosure_count_5d` 컬럼이 왜 전 행 0 인지 진단한다.

사용(컨테이너): docker exec stock_xgboost_ml python /app/scripts/_event_axis_probe.py
"""
from __future__ import annotations

import os

import numpy as np
import psycopg2

PANEL = os.environ.get("PROBE_PANEL", "/app/app/models/wf/panel_420_asof3.npz")


def pg():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    z = np.load(PANEL, allow_pickle=True)
    codes = sorted(set(str(c) for c in z["codes"]))
    dates = sorted(set(str(d)[:10] for d in z["dates"]))
    print(f"panel={PANEL} stocks={len(codes)} days={len(dates)} {dates[0]}~{dates[-1]}")
    conn = pg()
    cur = conn.cursor()
    ph = "(" + ",".join(["%s"] * len(codes)) + ")"
    s, e = dates[0], dates[-1]

    # 1) event_features 일자 분포 (패널 유니버스)
    cur.execute(
        f"SELECT count(distinct trade_date), min(trade_date), max(trade_date) "
        f"FROM event_features WHERE stock_code IN {ph}", codes)
    print("event_features: distinct_days,min,max =", cur.fetchone(), f" (panel days={len(dates)})")

    cur.execute(
        f"SELECT trade_date, count(*) FROM event_features WHERE stock_code IN {ph} "
        f"GROUP BY 1 ORDER BY 1 DESC LIMIT 8", codes)
    print("event_features 최근 일자:", cur.fetchall())

    # 2) event_features 비영 비율 (패널 유니버스, 패널 구간)
    cur.execute(
        f"SELECT count(*), sum((disclosure_count_5d<>0)::int), sum((event_stake_change_5d<>0)::int), "
        f"sum((event_contract_5d<>0)::int), sum((event_exec_change_5d<>0)::int) "
        f"FROM event_features WHERE stock_code IN {ph} AND trade_date>=%s AND trade_date<=%s",
        (*codes, s, e))
    print("event_features NZ (panel univ/window):", cur.fetchone())

    # 3) disclosures 원천 커버리지
    cur.execute(
        f"SELECT count(*), count(distinct stock_code) FROM disclosures "
        f"WHERE stock_code IN {ph} AND rcept_dt>=%s AND rcept_dt<=%s", (*codes, s, e))
    print("disclosures (panel univ/window):", cur.fetchone())

    # 4) 패널 컬럼 상태
    names = [str(n) for n in z["feature_names"]]
    X = z["X"]
    for f in ("disclosure_count_5d", "news_count_5d", "event_stake_change_5d"):
        if f in names:
            i = names.index(f)
            nz = int(np.count_nonzero(X[:, i]))
            print(f"panel col {f:24s} nonzero={nz}/{X.shape[0]}")
        else:
            print(f"panel col {f:24s} ABSENT")

    # 5) as-of 재계산 시 기대 비영 비율 (disclosure_count_5d)
    cur.execute(
        """
        SELECT count(*) FROM (
          SELECT d.trade_date, p.stock_code
          FROM (SELECT generate_series(%s::date, %s::date, '1 day')::date AS trade_date) d
          CROSS JOIN (SELECT unnest(%s::text[]) AS stock_code) p
          WHERE EXISTS (
            SELECT 1 FROM disclosures x
            WHERE x.stock_code = p.stock_code
              AND x.rcept_dt <= d.trade_date
              AND x.rcept_dt >= d.trade_date - INTERVAL '5 days')
        ) t
        """, (s, e, codes))
    print("as-of disclosure_count_5d > 0 예상 셀 수:", cur.fetchone()[0],
          f"/ {len(codes) * len(dates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
