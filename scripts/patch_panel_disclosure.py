#!/usr/bin/env python3
"""패널의 **공시(disclosure) 컬럼만** as-of 규칙으로 재계산한다 (전체 재빌드 대체).

WHY (2026-10-02 실측, CG67)
- panel_420_asof3 의 `disclosure_count_5d` 는 **전 행 0**(0/13,609)인데, 같은 유니버스·같은
  구간에서 `disclosures`(1,712행·49종목 전원)로 as-of 재계산하면 **5,816/13,769 = 42.2%** 셀이
  비영이 된다 → 데이터 부재가 아니라 **패널 빌드 시점에 원천이 비어 있었던 배선 결함**이다
  (base 패널 panel_420_asofpatch 는 2026-09-24 빌드, `disclosures` 는 그 뒤 채워졌다).
- 전체 재빌드는 13,609 페어에 85분+ 이고 그 사이 컨테이너 재시작에 취약하다 → 컬럼만 다시
  계산하면 수 분 + 같은 행 위 A/B 라 통제가 깨끗하다(patch_panel_asof 와 같은 논리).

as-of 규칙은 `app/feature_engine/sentiment_features.get_disclosure_count` 와 **동일**하게 둔다:
``rcept_dt <= date AND rcept_dt >= date - 5 days`` (미래 공시를 절대 쓰지 않는다).

사용 (xgboost-ml 컨테이너, cwd=/app):
    docker exec stock_xgboost_ml python /app/scripts/patch_panel_disclosure.py \
        --in app/models/wf/panel_420_asof3.npz --out app/models/wf/panel_420_asof4ev.npz
"""
from __future__ import annotations

import argparse
import bisect
import os

import numpy as np
import psycopg2


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="패널 공시 컬럼 as-of 재계산")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--limit", type=int, default=0, help="앞 N행만 처리(스모크, 0=전체)")
    ap.add_argument("--field", default="disclosure_count_5d")
    ap.add_argument("--window-days", type=int, default=5)
    args = ap.parse_args()

    z = np.load(args.src, allow_pickle=True)
    X = z["X"].astype(np.float64, copy=True)
    names = [str(n) for n in z["feature_names"]]
    dates = [str(d)[:10] for d in z["dates"]]
    codes = [str(c) for c in z["codes"]]

    if args.field not in names:
        print(f"STOP: 컬럼 {args.field} 이 패널에 없음 → {names[:5]} …")
        return 1
    col = names.index(args.field)

    conn = _pg_connect()
    cur = conn.cursor()
    uniq = sorted(set(codes))
    ph = "(" + ",".join(["%s"] * len(uniq)) + ")"
    cur.execute(f"SELECT stock_code, rcept_dt FROM disclosures WHERE stock_code IN {ph} "
                f"ORDER BY stock_code, rcept_dt", uniq)
    by_stock: dict[str, list[str]] = {}
    for code, dt in cur.fetchall():
        by_stock.setdefault(str(code), []).append(str(dt)[:10])
    conn.close()

    n = len(codes) if not args.limit else min(args.limit, len(codes))
    before_nz = int(np.count_nonzero(X[:n, col]))
    after_nz = 0
    for i in range(n):
        seq = by_stock.get(codes[i])
        if not seq:
            continue
        d = dates[i]
        lo = (np.datetime64(d) - np.timedelta64(args.window_days, "D")).astype(str)
        # seq 는 오름차순 → [lo, d] 구간 개수
        hi = bisect.bisect_right(seq, d)
        lo_i = bisect.bisect_left(seq, lo)
        v = hi - lo_i
        X[i, col] = float(v)
        if v:
            after_nz += 1

    print(f"{args.field}: rows={n} before_nonzero={before_nz} after_nonzero={after_nz} "
          f"({100.0 * after_nz / max(n, 1):.2f}%)")
    np.savez_compressed(args.dst, X=X.astype(np.float32), feature_names=z["feature_names"],
                        dates=z["dates"], codes=z["codes"], price=z["price"])
    print("saved:", args.dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
