import os

import sys,os,json,time
sys.path.insert(0,'/app')
from services.backtester.runner import BacktestRunner
import psycopg2
conn = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password=os.environ.get("POSTGRES_PASSWORD", ""))
cur = conn.cursor()
cur.execute("SELECT stock_code FROM stocks WHERE market='KOSDAQ'")
stocks=[r[0] for r in cur.fetchall()]; cur.close(); conn.close()
print(f'KOSDAQ stocks: {len(stocks)}')
t0=time.time()
runner=BacktestRunner(model_dir='/app/app/models/saved_models')
result=runner.run_backtest('ml_swing',stocks,'2026-05-01','2026-07-23')
elapsed=time.time()-t0
m={'strategy':result.strategy,'total_return':result.total_return,'sharpe_ratio':result.sharpe_ratio,'max_drawdown':result.max_drawdown,'win_rate':result.win_rate,'num_trades':result.num_trades,'total_stocks':len(stocks),'elapsed_seconds':round(elapsed,1)}
print(json.dumps(m,indent=2))
os.makedirs('/app/.omo/evidence',exist_ok=True)
json.dump(m,open('/app/.omo/evidence/kosdaq-backtest-result.json','w'),indent=2)
print('Saved!')
