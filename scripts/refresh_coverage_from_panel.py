#!/usr/bin/env python3
"""패널 실측값으로 feature_coverage 를 다시 쓴다.

WHY: feature_coverage 는 빌더마다 조각으로 갱신되어, 어떤 피처는 값이 며칠 전에 멈춘 채
'expect dead'로 남는다(2026-11-25 실측: 6개 피처가 11-23 값 고정). 패널(npy)이 있으면
그 피처의 실제 nonzero/상수 비율을 계산할 수 있으므로, 패널에 존재하는 컬럼만 덮어쓴다.

사용:
    python3 scripts/refresh_coverage_from_panel.py --panel data/panels/panel_prod200_v8.npz
    ... --dry-run   (DB 쓰기 없이 계산 결과만 출력)
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import psycopg2


def load_env(path: str = "/home/jhshi/analyist_dd/.env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def pg_connect():
    load_env()
    host = os.environ.get("POSTGRES_HOST") or "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434"))
    if not os.path.exists("/.dockerenv"):
        host = "127.0.0.1" if host == "postgres" else host
        port = 5434 if port == 5432 else port
    return psycopg2.connect(host=host, port=port, dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
                            user=os.environ.get("POSTGRES_USER", "stock_user"),
                            password=os.environ.get("POSTGRES_PASSWORD", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="data/panels/panel_prod200_v8.npz")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="쉼표 구분 컬럼 제한")
    args = ap.parse_args()

    z = np.load(args.panel, allow_pickle=True)
    names = [str(x) for x in z["feature_names"]]
    X = z["X"].astype(float)
    only = {c.strip() for c in args.only.split(",") if c.strip()}

    stats = {}
    for i, name in enumerate(names):
        if only and name not in only:
            continue
        col = X[:, i]
        finite = np.isfinite(col)
        if finite.sum() == 0:
            stats[name] = (0.0, 1.0)
            continue
        vals = col[finite]
        nz = float((vals != 0).mean())
        const = 1.0 if vals.std() == 0 else 0.0
        stats[name] = (nz, const)

    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("select feature_name from feature_coverage")
    known = {r[0] for r in cur.fetchall()}
    updated = 0
    for name, (nz, const) in stats.items():
        if name not in known:
            continue
        if not args.dry_run:
            cur.execute("""
                UPDATE feature_coverage
                   SET nonzero_ratio = %s, nonzero_ratio_naive = %s,
                       cross_section_constant_ratio = %s, computed_at = now()
                 WHERE feature_name = %s
            """, (nz, nz, const, name))
        updated += 1
        if nz < 0.01:
            print(f"  {name:26s} nonzero {nz*100:6.3f}% constant {const:.2f}")
    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"{'(dry-run) ' if args.dry_run else ''}feature_coverage 갱신 {updated}개 / 패널 컬럼 {len(stats)}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
