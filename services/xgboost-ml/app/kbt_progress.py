import sys, os, json, time, logging
sys.path.insert(0, "/app")
from services.backtester.runner import BacktestRunner
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

LOG = "/tmp/kbt_nohup_100.log"
RESULT = "/app/.omo/evidence/kosdaq-backtest-top100.json"

def log(msg):
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a") as f:
        f.write(f"[{t}] {msg}\n")
    print(f"[{t}] {msg}", flush=True)

log("=== KOSDAQ Backtest TOP 100 ===")

conn = psycopg2.connect(host="postgres", port=5432, dbname="stock_trading", user="stock_user", password="stock_secure_password_2026")
cur = conn.cursor()
cur.execute("""
    SELECT md.stock_code FROM market_data md
    JOIN stocks s ON s.stock_code = md.stock_code
    WHERE s.market = %s AND md.trade_date >= %s AND md.trade_date <= %s
    GROUP BY md.stock_code ORDER BY AVG(md.volume) DESC LIMIT 100
""", ("KOSDAQ", "2026-05-01", "2026-07-23"))
stocks = [r[0] for r in cur.fetchall()]
cur.close()
conn.close()
log(f"Top 100 KOSDAQ stocks selected: {stocks[:5]}...")

t0 = time.time()
runner = BacktestRunner(model_dir="/app/app/models/saved_models")
result = runner.run_backtest("ml_swing", stocks, "2026-05-01", "2026-07-23")
elapsed = time.time() - t0

m = {
    "strategy": result.strategy,
    "start_date": str(result.start_date),
    "end_date": str(result.end_date),
    "total_return": result.total_return,
    "sharpe_ratio": result.sharpe_ratio,
    "max_drawdown": result.max_drawdown,
    "win_rate": result.win_rate,
    "num_trades": result.num_trades,
    "total_stocks": len(stocks),
    "elapsed_seconds": round(elapsed, 1),
    "trades_preview": [(t.stock_code, str(t.date), t.signal, round(float(t.confidence),3), round(float(t.pnl),4)) for t in result.trades[:30]]
}
log(json.dumps(m, indent=2))

os.makedirs(os.path.dirname(RESULT), exist_ok=True)
json.dump(m, open(RESULT, "w"), indent=2)
log(f"Result saved! trades={result.num_trades}, return={result.total_return:.4f}, sharpe={result.sharpe_ratio:.4f}")
log("=== KOSDAQ Backtest TOP 100 END ===")
