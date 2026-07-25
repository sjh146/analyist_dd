import sys, os, json, time, logging
sys.path.insert(0, "/app")
import psycopg2
from types import MethodType

# Monkey-patch BacktestRunner to use threshold 0.55
from services.backtester.runner import BacktestRunner

original_run = BacktestRunner.run_backtest

def patched_run(self, strategy, stock_codes, start_date, end_date):
    """Same as original but with 0.55 threshold."""
    import numpy as np
    from datetime import date
    
    try:
        df = self.pipeline.build_training_features(stock_codes, start_date, end_date)
        if df is None or df.empty:
            return self._empty_result(strategy, start_date, end_date)
        
        saved_features = self.ensemble.load_feature_names(self.model_dir)
        available_features = [f for f in saved_features if f in df.columns]
        if len(available_features) < 5:
            return self._empty_result(strategy, start_date, end_date)
        
        X = df[available_features].values.astype(np.float32)
        X = np.nan_to_num(X, nan=0.0)
        probs = self.ensemble.predict(X)
        
        from services.backtester.runner import BacktestTrade, BacktestResult
        trades = []
        daily_returns = []
        
        if 'stock_code' in df.columns and 'price' in df.columns:
            for stock_code in stock_codes:
                mask = df['stock_code'] == stock_code
                stock_df = df[mask].copy()
                stock_probs = probs[mask.values]
                if stock_df.empty:
                    continue
                prices = stock_df['price'].values
                stock_dates = stock_df['date'].values if 'date' in stock_df.columns else [None] * len(stock_df)
                for i in range(len(stock_df)):
                    prob = float(stock_probs[i])
                    if prob >= 0.55:  # <-- LOWERED THRESHOLD
                        if i + 5 < len(prices) and prices[i] > 0:
                            actual_ret = float((prices[min(i + 5, len(prices) - 1)] - prices[i]) / prices[i])
                        else:
                            actual_ret = 0.0
                        trade_date_val = stock_dates[i]
                        if isinstance(trade_date_val, str):
                            td = date.fromisoformat(trade_date_val)
                        elif hasattr(trade_date_val, 'date'):
                            td = trade_date_val.date()
                        else:
                            td = date.today()
                        predicted_ret = float(prob - 0.5)
                        pnl = actual_ret
                        trade = BacktestTrade(date=td, stock_code=stock_code, signal='buy',
                                              confidence=prob, predicted_return=predicted_ret,
                                              actual_return=actual_ret, pnl=pnl)
                        trades.append(trade)
                        daily_returns.append(actual_ret)
        
        if not daily_returns:
            return self._empty_result(strategy, start_date, end_date)
        
        returns_array = np.array(daily_returns)
        total_return = float(np.sum(returns_array))
        mean_ret = float(np.mean(returns_array))
        std_ret = float(np.std(returns_array))
        sharpe = (mean_ret / (std_ret + 1e-8)) * np.sqrt(252)
        cumulative = np.cumprod(1 + returns_array)
        peak = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - peak) / peak
        max_dd = float(np.min(drawdown))
        win_rate = float(np.mean(returns_array > 0))
        
        return BacktestResult(strategy=strategy, start_date=date.fromisoformat(start_date),
                              end_date=date.fromisoformat(end_date), total_return=total_return,
                              sharpe_ratio=sharpe, max_drawdown=max_dd, win_rate=win_rate,
                              num_trades=len(trades), trades=trades)
    except Exception as e:
        logging.exception(f"Backtest failed: {e}")
        return self._empty_result(strategy, start_date, end_date)

BacktestRunner.run_backtest = patched_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = "/tmp/kbt_low_threshold.log"
RESULT = "/app/.omo/evidence/kosdaq-backtest-threshold-055.json"

def log(msg):
    t = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a") as f:
        f.write(f"[{t}] {msg}\n")
    print(f"[{t}] {msg}", flush=True)

log("=== KOSDAQ Backtest (threshold=0.55) ===")
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
log(f"Top 100 KOSDAQ stocks (threshold=0.55)")

t0 = time.time()
runner = BacktestRunner(model_dir="/app/app/models/saved_models")
result = runner.run_backtest("ml_swing", stocks, "2026-05-01", "2026-07-23")
elapsed = time.time() - t0

m = {
    "strategy": result.strategy,
    "threshold": 0.55,
    "start_date": str(result.start_date),
    "end_date": str(result.end_date),
    "total_return": result.total_return,
    "sharpe_ratio": result.sharpe_ratio,
    "max_drawdown": result.max_drawdown,
    "win_rate": result.win_rate,
    "num_trades": result.num_trades,
    "total_stocks": len(stocks),
    "elapsed_seconds": round(elapsed, 1),
    "trades_preview": [(t.stock_code, str(t.date), round(float(t.confidence),3), round(float(t.pnl),4)) for t in result.trades[:50]]
}
log(json.dumps(m, indent=2))
os.makedirs(os.path.dirname(RESULT), exist_ok=True)
json.dump(m, open(RESULT, "w"), indent=2)
log(f"Done! trades={result.num_trades}, return={result.total_return:.4f}, sharpe={result.sharpe_ratio:.4f}, win_rate={result.win_rate:.4f}")
