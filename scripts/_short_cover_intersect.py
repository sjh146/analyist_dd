#!/usr/bin/env python3
"""공매도(krx_short_selling)·수급(foreign_institutional) 원천의 유니버스 ∩ 패널 종목 교집합 실측.

왜: 패널의 short_selling_ratio 비영이 0.59% 인데 원천 테이블(krx_short_selling)은 201종목·
16,214행(비영 97.95%)으로 살아 있다 → '구현 문제'인지 '유니버스 불일치'인지 갈라야 한다.
교집합이 작으면 데이터 부재가 아니라 유니버스 문제이고, 교집합이 크면 배관 문제다.

실행: docker exec stock_xgboost_ml python /app/scripts/_short_cover_intersect.py
"""
import os

import numpy as np
import psycopg2

P = "/app/app/models/wf/panel_995.npz"
d = np.load(P, allow_pickle=True)
panel_codes = sorted({str(c) for c in d["codes"]})
print(f"패널 종목 {len(panel_codes)}개: {panel_codes[:8]} ...")

conn = psycopg2.connect(
    host=os.environ.get("POSTGRES_HOST", "postgres"),
    port=int(os.environ.get("POSTGRES_PORT", 5432)),
    dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    user=os.environ.get("POSTGRES_USER", "stock_user"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
)
cur = conn.cursor()

for tbl, dcol in (("krx_short_selling", "trade_date"),
                  ("foreign_institutional", "trade_date")):
    cur.execute(f"SELECT count(*) , count(DISTINCT stock_code) FROM public.{tbl}")
    rows, codes = cur.fetchone()
    cur.execute(f"SELECT DISTINCT stock_code FROM public.{tbl}")
    src = {r[0] for r in cur.fetchall()}
    inter = sorted(src & set(panel_codes))
    print(f"\n{tbl}: {rows}행 · {codes}종목 → 패널 49종목 교집합 {len(inter)}개 {inter[:10]}")
    if inter:
        cur.execute(
            f"SELECT stock_code, count(*), min({dcol}), max({dcol}) FROM public.{tbl} "
            f"WHERE stock_code = ANY(%s) GROUP BY 1 ORDER BY 2 DESC LIMIT 8", (inter,))
        for r in cur.fetchall():
            print("   ", r)

# 패널 내 short_selling_ratio 가 0 이 아닌 행의 날짜
names = [str(x) for x in d["feature_names"]]
if "short_selling_ratio" in names:
    i = names.index("short_selling_ratio")
    col = d["X"][:, i]
    nz = np.nonzero(col)[0]
    dates = [str(x) for x in d["dates"]]
    codes = [str(x) for x in d["codes"]]
    print(f"\n패널 short_selling_ratio 비영 {len(nz)}행")
    for j in nz[:5]:
        print("   ", codes[j], dates[j], col[j])
conn.close()
