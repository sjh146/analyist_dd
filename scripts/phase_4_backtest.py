"""Phase 4 모델 백테스트 — 층화 랜덤 유니버스 + 챔피언 json 계약 순서 행렬.

2026-08 수정: prepare_training_data의 분산 필터가 상수 피처를 제거해 fnames가
챔피언(173)보다 좁아져 CatBoostError("Feature N present in model but not in pool")
발생. 여기서는 챔피언 feature_names.json 순서 그대로 0-fill 행렬을 구성한다
(retrain_champion과 동일한 계약) — 트레이너 필터와 무관하게 항상 폭 일치.
"""
import sys, json, os
from datetime import datetime, timedelta
import numpy as np
import psycopg2

sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.training.trainer import Trainer
from app.training.universe import select_backtest_universe
from sklearn.metrics import roc_auc_score, accuracy_score

pg = psycopg2.connect(host='postgres', port=5432, dbname='stock_trading',
                      user='stock_user', password=os.environ.get('POSTGRES_PASSWORD', ''))
stocks_list = select_backtest_universe(pg, n_kospi=30, n_kosdaq=20, min_days=30, seed=42)
print(f'Backtest universe: {len(stocks_list)} stocks')

pipeline = FeaturePipeline(pg_conn=pg)
trainer = Trainer(storage=None, feature_pipeline=pipeline)
end = datetime.now()
start = end - timedelta(days=90)
df = pipeline.build_training_features(
    stocks_list, start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')
)
if df is None or len(df) < 100:
    print(f'Backtest FAILED - insufficient data: {0 if df is None else len(df)}')
    sys.exit(1)
print(f'panel rows: {len(df)}, columns: {len(df.columns)}')

# 날짜 정렬 (시계열 무결성)
if 'date' in df.columns:
    df = df.sort_values('date').reset_index(drop=True)
elif 'trade_date' in df.columns:
    df = df.sort_values('trade_date').reset_index(drop=True)

y = trainer._create_labels(df)

# 챔피언 계약 행렬: json 순서 그대로 0-fill (분산 필터 없음)
ensemble = EnsembleModel(model_dir='app/models/champion')
ensemble.load('app/models/champion')
model_f = ensemble.load_feature_names('app/models/champion')
X = np.zeros((len(df), len(model_f)), dtype=np.float32)
for j, f in enumerate(model_f):
    if f in df.columns:
        col = df[f].to_numpy(dtype=np.float64)
        X[:, j] = np.nan_to_num(col, nan=0.0, posinf=0.0, neginf=0.0)
print(f'matrix: {X.shape} (champion {len(model_f)} features)')

valid = ~np.isnan(y)
X, y = X[valid], y[valid]
probs = ensemble.predict(X)
auc = roc_auc_score(y, probs)
acc = accuracy_score(y, (probs >= 0.5).astype(int))
print(f'Backtest AUC: {auc:.4f}, ACC: {acc:.4f} (n={len(y)}, up_rate={y.mean():.3f})')

# 뉴스 피처 실제 반영 확인
news_feats = [f for f in model_f if 'market_impact' in f or f.startswith('event_') or 'theme_exposure' in f]
if news_feats:
    idxs = [model_f.index(f) for f in news_feats]
    sub = X[:, idxs]
    nz = (sub != 0).sum(axis=0)
    for f, c in zip(news_feats, nz):
        print(f'  news feature [{f}] nonzero rows: {c}/{len(sub)}')

result_dict = {"auc": round(auc, 4), "acc": round(acc, 4),
               "n_stocks": len(stocks_list), "n_rows": int(len(y)),
               "n_features": int(len(model_f))}
os.makedirs('/app/reports', exist_ok=True)
with open('/app/reports/backtest_result.json', 'w') as f:
    json.dump(result_dict, f, indent=2)
print('saved: /app/reports/backtest_result.json')

# 2026-08: 실행 결과 이력 기록 (Grafana Quant Strategy Monitoring 대시보드용)
try:
    sys.path.insert(0, '/app/scripts')
    from record_strategy_run import record_run
    record_run(
        tool="backtest",
        stocks=len(stocks_list),
        auc=round(auc, 4),
        accuracy=round(acc, 4),
        metric_value=round(auc, 4),
        meta={"n_rows": int(len(y)), "n_features": int(len(model_f)),
              "up_rate": round(float(y.mean()), 3), "universe": "kospi30+kosdaq20 seed42"},
    )
except Exception as e:
    print(f'[record_run] backtest 기록 실패(무시): {e}')
