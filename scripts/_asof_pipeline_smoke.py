#!/usr/bin/env python3
"""as-of 수리 e2e 스모크 — 실제 FeaturePipeline 경로에서 재무 피처가 시점에 따라 달라지는가.

수리 전: value_per·value_psr·quality_roa·quality_score 가 **모든 과거 날짜에서 동일**(빌드 시점
최신 스냅샷) = 종목 상수. 수리 후: 날짜를 바꾸면 그 날짜에 알 수 있었던 보고서를 쓴다.

사용: docker exec stock_xgboost_ml python /app/scripts/_asof_pipeline_smoke.py
"""
import os
import sys

import psycopg2

sys.path.insert(0, "/app")
os.chdir("/app")
from app.feature_engine.feature_pipeline import FeaturePipeline

KEYS = ["value_per", "value_pbr", "value_psr", "value_pcr", "quality_roa",
        "quality_f_score", "quality_asset_growth", "quality_score", "revenue", "roe"]

pg = psycopg2.connect(host="postgres", port=5432, dbname="stock_trading",
                      user="stock_user", password=os.environ.get("POSTGRES_PASSWORD", ""))
cur = pg.cursor()
cur.execute("""
    SELECT stock_code, count(*) n, min(report_date), max(report_date)
    FROM financial_statements GROUP BY stock_code HAVING count(*) >= 3
    ORDER BY n DESC LIMIT 1
""")
code, n, rmin, rmax = cur.fetchone()
cur.execute("SELECT min(trade_date), max(trade_date) FROM market_data WHERE stock_code = %s", (code,))
d0, d1 = cur.fetchone()
cur.close()
print(f"종목 {code} 보고서 {n}건 {rmin}~{rmax} · 시세 {d0}~{d1}")

pipe = FeaturePipeline(pg_conn=pg)
dates = [str(d1)[:10], str(d0)[:10]]
rows = {}
for d in dates + [None]:
    f = pipe.build_features(code, d)
    rows[str(d)] = {k: f.get(k) for k in KEYS}
    print(f"  date={d}: " + " ".join(f"{k}={rows[str(d)][k]}" for k in KEYS[:6]))

# 검사: 최신일과 최과거일의 값이 **다른** 컬럼이 하나 이상 있어야 한다(수리 전에는 전부 동일).
diff = [k for k in KEYS if rows[dates[0]][k] != rows[dates[1]][k]]
print(f"시점에 따라 달라진 컬럼 {len(diff)}개: {diff}")
# None 경로(추론 기본)는 최신일과 같아야 한다(오늘 = 최신 보고서).
same_none = [k for k in KEYS if rows["None"][k] == rows[dates[0]][k]]
print(f"date=None 이 최신일과 동일한 컬럼 {len(same_none)}/{len(KEYS)}: {same_none}")
print("OK" if diff else "FAIL: 재무 피처가 여전히 종목 상수(as-of 미적용)")
sys.exit(0 if diff else 1)
