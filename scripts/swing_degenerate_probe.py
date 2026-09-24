#!/usr/bin/env python3
"""swing_degenerate_probe — 배포 챔피언 모델의 확률 분포를 직접 측정한다.

배경(실측 2026-09-23): swing 후보 20건 중 17건의 confidence 가 정확히 0.5081 로 동일.
이는 '약한 모델'이 아니라 **피처가 비어 모델 출력이 상수로 붕괴**하는 현상이다.
이 프로브는 배포 경로와 동일하게(champion/feature_names.json 기준 벡터 구성,
결측은 0.0) 피처를 만들어 예측하고,
  ① 확률 분포(고유값 수·spread) ② 종목별 비영 피처 수 ③ 상수 예측 종목의 특징
을 출력한다.

실행: docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/swing_degenerate_probe.py [N]'
"""

import json
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np
import psycopg2

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel

MODEL_DIR = os.environ.get("MODEL_DIR", "app/models/champion")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 30
    codes_arg = ""
    for a in sys.argv[1:]:
        if a.startswith("--codes="):
            codes_arg = a.split("=", 1)[1]
    pg = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    cur = pg.cursor()
    if codes_arg:
        codes = [c.strip() for c in codes_arg.split(",") if c.strip()]
        cur.execute("""
            SELECT stock_code, stock_name, market, instrument_type
            FROM stocks WHERE stock_code = ANY(%s)
        """, (codes,))
    else:
        cur.execute("""
            WITH recent AS (
                SELECT stock_code, SUM(trading_value) AS tv
                FROM market_data
                WHERE trade_date >= (SELECT max(trade_date) - 20 FROM market_data)
                  AND trading_value IS NOT NULL
                GROUP BY stock_code
            )
            SELECT r.stock_code, s.stock_name, s.market, s.instrument_type
            FROM recent r JOIN stocks s ON r.stock_code = s.stock_code
            WHERE s.market = 'KOSDAQ'
            ORDER BY r.tv DESC, r.stock_code
            LIMIT %s
        """, (n,))
    stocks = cur.fetchall()
    cur.execute("SELECT max(trade_date)::text FROM market_data")
    latest = cur.fetchone()[0]
    print(f"[probe] 종목 {len(stocks)}개, 기준일 {latest}, 모델 {MODEL_DIR}")

    fnames = json.load(open(os.path.join(MODEL_DIR, "feature_names.json")))
    ens = EnsembleModel(model_dir=MODEL_DIR)
    ens.load(MODEL_DIR)
    pipe = FeaturePipeline(pg_conn=pg)

    rows = []
    for code, name, market, itype in stocks:
        try:
            f = pipe.build_features(code, latest)
        except Exception as e:
            print(f"  {code} 피처 실패: {e}")
            continue
        fv = np.nan_to_num(
            np.array([float(f.get(k, 0.0)) for k in fnames], dtype=np.float32), nan=0.0)
        nz = int(np.sum(fv != 0))
        try:
            prob = float(np.asarray(ens.predict(np.array([fv]))).ravel()[0])
        except Exception as e:
            print(f"  {code} 예측 실패: {e}")
            continue
        rows.append({"code": code, "name": name, "type": itype, "prob": prob,
                     "nz": nz, "n_feat": len(fv)})
        print(f"  {code} {str(name)[:14]:14s} {str(itype):5s} prob={prob:.4f} "
              f"비영피처={nz}/{len(fv)}")

    if rows:
        probs = np.array([r["prob"] for r in rows])
        uniq = len(set(np.round(probs, 6)))
        print(f"\n[분포] 고유값 {uniq}/{len(probs)} | min {probs.min():.4f} "
              f"max {probs.max():.4f} spread {probs.max() - probs.min():.4f} "
              f"std {probs.std():.4f}")
        import collections
        common = collections.Counter([round(p, 6) for p in probs]).most_common(3)
        print(f"[최빈 확률] {common}")
        nz = np.array([r["nz"] for r in rows])
        print(f"[비영 피처] min {nz.min()} median {int(np.median(nz))} max {nz.max()} "
              f"(모델 피처 {len(fnames)}개 기준)")
        deg = [r for r in rows if r["nz"] < 10]
        print(f"[붕괴 의심(비영<10)] {len(deg)}종목 "
              f"{[(r['code'], r['type']) for r in deg][:8]}")
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
