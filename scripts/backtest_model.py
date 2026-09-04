"""모델 백테스트 (Phase 4) — 챔피언 앙상블 홀드아웃 패널 AUC/ACC.

유니버스: 층화 랜덤 KOSPI30 + KOSDAQ20 (seed 42, ETF/ETN 제외).
패널: 최근 90 거래일. 날짜별 2-pass (피처 빌드 → 크로스섹션 랭크 → 벡터 → 예측),
레이블: 다음 거래일 상승 여부 (close[t+1] > close[t]).
피처 벡터는 champion/feature_names.json 순서 0-fill (스크리너 추론 계약과 동일).
"""
import json
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import psycopg2

sys.path.insert(0, "/app")

from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402
from app.models.ensemble_model import EnsembleModel  # noqa: E402
from app.training.universe import select_backtest_universe  # noqa: E402
from sklearn.metrics import accuracy_score, roc_auc_score  # noqa: E402

pg = psycopg2.connect(
    host="postgres", port=5432, dbname="stock_trading",
    user="stock_user", password=os.environ.get("POSTGRES_PASSWORD", ""),
)
cur = pg.cursor()

# 1) 유니버스 (층화 랜덤 50, seed 42)
stocks = select_backtest_universe(pg, n_kospi=30, n_kosdaq=20, seed=42)
print(f"Backtest universe: {len(stocks)} stocks", flush=True)

# 2) 최근 90 거래일 (각 종목 공통으로 존재하는 날짜)
cur.execute(
    "SELECT DISTINCT trade_date FROM market_data ORDER BY trade_date DESC LIMIT 95"
)
dates = sorted(r[0] for r in cur.fetchall())
panel_dates = dates[-90:]
print(f"Panel: {len(panel_dates)} trading days ({panel_dates[0]} ~ {panel_dates[-1]})", flush=True)

# 3) close 가격 전부 로드 (레이블용)
cur.execute(
    "SELECT stock_code, trade_date, close_price FROM market_data "
    "WHERE stock_code = ANY(%s) AND trade_date >= %s",
    (stocks, panel_dates[0]),
)
close_map: dict[str, dict] = {}
for code, d, c in cur.fetchall():
    # 키를 ISO 문자열로 통일 (아래 ds와 일치)
    close_map.setdefault(code, {})[str(d)] = float(c)
cur.close()

pipe = FeaturePipeline(pg_conn=pg)
en = EnsembleModel(model_dir="app/models/champion")
en.load("app/models/champion")
feature_names = en.load_feature_names("app/models/champion")
print(f"Model: champion, features={len(feature_names)}", flush=True)

# 4) 날짜별 2-pass: 피처 빌드 → 랭크 주입 → 벡터 → 예측 / 레이블
probs, labels, codes = [], [], []
errors = 0
for di, d in enumerate(panel_dates):
    if di + 1 >= len(panel_dates):
        break  # 마지막 날은 t+1 레이블 없음
    nxt = panel_dates[di + 1]
    ds = str(d.date() if hasattr(d, "date") else d)
    nds = str(nxt.date() if hasattr(nxt, "date") else nxt)

    features_by_code = {}
    for code in stocks:
        try:
            features = pipe.build_features(code, ds)
            if not features or features.get("feature_count", 0) < 10:
                continue
            features_by_code[code] = features
        except Exception:
            errors += 1
            continue
    if not features_by_code:
        continue
    pipe.compute_cross_sectional_ranks(features_by_code)

    for code, features in features_by_code.items():
        c_t = close_map.get(code, {}).get(ds)
        c_n = close_map.get(code, {}).get(nds)
        if c_t is None or c_n is None:
            continue
        try:
            fv = np.array(
                [float(features.get(f, 0.0)) for f in feature_names],
                dtype=np.float32,
            )
            fv = np.nan_to_num(fv, nan=0.0)
            prob = float(en.predict(np.array([fv]))[0])
            probs.append(prob)
            labels.append(1 if c_n > c_t else 0)
            codes.append(code)
        except Exception:
            errors += 1
            continue

probs = np.array(probs)
labels = np.array(labels)
auc = roc_auc_score(labels, probs) if len(np.unique(labels)) > 1 else float("nan")
acc = accuracy_score(labels, (probs > 0.5).astype(int))

report = {
    "generated_at": datetime.utcnow().isoformat() + "Z",
    "universe": stocks,
    "panel": [str(d) for d in panel_dates],
    "n_samples": int(len(probs)),
    "n_stocks": len(stocks),
    "n_errors": errors,
    "up_rate": float(labels.mean()),
    "auc": float(auc),
    "accuracy": float(acc),
    "feature_count": len(feature_names),
}
os.makedirs("/app/reports", exist_ok=True)
with open("/app/reports/backtest_result.json", "w") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(f"\n=== MODEL BACKTEST ===")
print(f"samples={len(probs)} errors={errors} up_rate={labels.mean():.3f}")
print(f"AUC={auc:.4f} ACC={acc:.4f}")
print(f"report -> /app/reports/backtest_result.json")
