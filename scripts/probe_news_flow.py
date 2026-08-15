"""뉴스 데이터 흐름 프로브 — 뉴스 이벤트가 있는 종목의 피처에 실제 반영되는지 검증.

뉴스 있는 종목 vs 뉴스 없는 종목의 market_impact_score / event_*_5d / theme_exposure_5d 비교.
"""
import sys, os
import numpy as np
import psycopg2

sys.path.insert(0, '/app')
os.chdir('/app')
from app.feature_engine.feature_pipeline import FeaturePipeline

pg = psycopg2.connect(host='postgres', port=5432, dbname='stock_trading',
                      user='stock_user', password=os.environ.get('POSTGRES_PASSWORD', ''))
pipe = FeaturePipeline(pg_conn=pg)

# 1) 뉴스 이벤트가 있는 종목 (최근 7일)
cur = pg.cursor()
cur.execute("""
    SELECT DISTINCT stock_code FROM news_events
    WHERE event_date >= CURRENT_DATE - 7 AND stock_code IS NOT NULL
    ORDER BY stock_code LIMIT 8
""")
news_stocks = [r[0] for r in cur.fetchall()]
# 2) 뉴스 없는 종목 (최근 7일 이벤트 없음) — 유니버스에서 무작위
cur.execute("""
    SELECT s.stock_code FROM stocks s
    WHERE s.market IN ('KOSPI','KOSDAQ')
      AND NOT EXISTS (SELECT 1 FROM news_events ne WHERE ne.stock_code = s.stock_code AND ne.event_date >= CURRENT_DATE - 7)
      AND EXISTS (SELECT 1 FROM market_data md WHERE md.stock_code = s.stock_code AND md.trade_date >= CURRENT_DATE - 10)
    ORDER BY random() LIMIT 8
""")
no_news_stocks = [r[0] for r in cur.fetchall()]
cur.close()

news_feats = ['market_impact_score'] + [f for f in pipe.get_feature_names() if f.startswith('event_')][:4] + ['theme_exposure_5d']

def probe(stock_code):
    feats = pipe.build_features(stock_code, None)
    out = {}
    for f in news_feats:
        v = feats.get(f, 0.0)
        out[f] = round(float(v), 4) if isinstance(v, (int, float)) else v
    return out

print('=== 뉴스 이벤트 있는 종목 ===')
for c in news_stocks:
    try:
        print(f'  {c}: {probe(c)}')
    except Exception as e:
        print(f'  {c}: build 실패 {type(e).__name__}: {str(e)[:60]}')

print('=== 뉴스 없는 종목 (대조군) ===')
for c in no_news_stocks:
    try:
        print(f'  {c}: {probe(c)}')
    except Exception as e:
        print(f'  {c}: build 실패 {type(e).__name__}: {str(e)[:60]}')
