import sys, os, json
from datetime import datetime, timedelta
import psycopg2
sys.path.insert(0, '/app')
os.chdir('/app')
from app.feature_engine.feature_pipeline import FeaturePipeline

pg = psycopg2.connect(host='postgres', port=5432, dbname='stock_trading',
                      user='stock_user', password=os.environ.get('POSTGRES_PASSWORD', ''))
pipe = FeaturePipeline(pg_conn=pg)

# 1) 스크리너 경로 (market_df 없음 — DB에서 직접 로드)
feats = pipe.build_features('005930', None)
print('build_features(스크리너 경로):', feats.get('feature_count'), '피처')

# 2) 배치 경로 (market_df 전달)
df = pipe.build_training_features(['005930'], '2026-06-01', '2026-08-11')
print('build_training_features: rows', len(df), '| 컬럼', len(df.columns))
miss = [f for f in pipe.get_feature_names() if f not in df.columns]
print('배치에서 빠진 피처:', len(miss))
print('샘플:', miss[:15])
