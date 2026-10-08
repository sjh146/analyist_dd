"""공매도(krx_short_selling) 수집 범위 ↔ 패널/학습유니버스 정합 프로브 (읽기 전용).

용도: '죽은 피처'가 배선 결함인지 수집 범위 갭인지 분리한다. CG67(disclosure 배선)/CG73(뉴스·SNS 범위)
계열 진단. 피처 컬럼의 비영률이 낮을 때 먼저 이걸 돌려라.

실행(컨테이너): docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/short_selling_scope_probe.py
                 [--panel /app/app/models/wf/panel_prod200.npz]   (기본값 = env PANEL 또는 panel_prod200)

출력: ① 소스 컬럼별 nonnull/nonzero(소스 건강도) ② 종목/날짜 교집합 ③ 패널 컬럼 비영률
④ (소스 있음 & 패널 0) = 배선 결함 후보 수, (소스 없음 & 패널 비영) = 0 이어야 정상.
"""
import argparse
import os
import sys

import numpy as np
import psycopg2

DEFAULT_PANEL = os.environ.get("PANEL", "/app/app/models/wf/panel_prod200.npz")
COL = "short_selling_ratio"


def db():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=DEFAULT_PANEL)
    a = ap.parse_args()

    if not os.path.exists(a.panel):
        print(f"[SKIP] panel 없음: {a.panel}"); return 2
    z = np.load(a.panel, allow_pickle=True)
    names = [str(x) for x in z["feature_names"]]
    if COL not in names:
        print(f"[SKIP] 패널에 {COL} 컬럼 없음"); return 2

    codes = [str(c) for c in z["codes"]]
    dates = [str(d)[:10] for d in z["dates"]]
    col = z["X"][:, names.index(COL)].astype(float)
    print(f"panel={os.path.basename(a.panel)} X={z['X'].shape} codes={len(set(codes))} "
          f"dates {min(dates)}~{max(dates)}")
    print(f"{COL}: nonzero {100.0*(col!=0).mean():.4f}%  mean {col.mean():.6f}  max {col.max():.4f}")

    conn = db(); cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='krx_short_selling'")
    cols = [r[0] for r in cur.fetchall()]
    print("krx_short_selling columns:", cols)
    cur.execute("SELECT count(*), count(distinct stock_code), min(trade_date), max(trade_date) FROM krx_short_selling")
    n, nc, d0, d1 = cur.fetchone()
    print(f"source rows={n} codes={nc} range {d0}~{d1}")
    for c in ("short_volume", "total_volume", "short_ratio", "balance_quantity"):
        if c not in cols:
            continue
        cur.execute(f"SELECT count({c}), sum(CASE WHEN {c}<>0 THEN 1 ELSE 0 END) FROM krx_short_selling")
        nn, nz = cur.fetchone()
        print(f"  {c:18} nonnull={nn} nonzero={nz}")

    cur.execute("SELECT DISTINCT stock_code FROM krx_short_selling")
    kcodes = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT trade_date FROM krx_short_selling")
    kdates = {str(r[0])[:10] for r in cur.fetchall()}
    pcodes, pdates = set(codes), set(dates)
    print(f"code intersection: {len(pcodes & kcodes)}/{len(pcodes)}  "
          f"date intersection: {len(pdates & kdates)}/{len(pdates)}")

    cur.execute("""SELECT stock_code, trade_date, short_volume, total_volume
                   FROM krx_short_selling WHERE stock_code = ANY(%s)""", (list(pcodes),))
    src = {(str(r[0]), str(r[1])[:10]): (r[2], r[3]) for r in cur.fetchall()}
    gap = 0        # 소스 있음 & 패널 0 → 배선 결함 후보
    exp_nonzero = 0
    for i, (c, d) in enumerate(zip(codes, dates)):
        sv, tv = src.get((c, d), (None, None))
        if sv and tv and float(tv) > 0:
            exp_nonzero += 1
            if col[i] == 0:
                gap += 1
    print(f"expected nonzero after wiring: {100.0*exp_nonzero/len(codes):.2f}%  "
          f"| source-present & panel==0 (배선 결함 후보): {gap}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
