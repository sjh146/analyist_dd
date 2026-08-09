import logging
from datetime import datetime, date
from typing import Dict, Optional

import backtrader as bt
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PGDataFeed(bt.feeds.PandasData):
    params = (
        ('datetime', None),
        ('open', 'open'),
        ('high', 'high'),
        ('low', 'low'),
        ('close', 'close'),
        ('volume', 'volume'),
        ('openinterest', -1),
    )

    def __init__(self, **kwargs):
        stock_code = kwargs.pop('stock_code', None)
        start_date = kwargs.pop('start_date', None)
        end_date = kwargs.pop('end_date', None)
        pg_conn = kwargs.pop('pg_conn', None)

        self.stock_code = stock_code
        if pg_conn is not None:
            df = self._fetch_from_db(pg_conn, start_date, end_date, stock_code)
        else:
            df = self._generate_synthetic(start_date, end_date)
        df.index = pd.to_datetime(df['datetime'])

        self.p.dataname = df
        super().__init__()

    @staticmethod
    def _fetch_from_db(pg_conn, start_date: str, end_date: str, stock_code: str = None) -> pd.DataFrame:
        query = """
            SELECT trade_date, open_price AS open, high_price AS high, low_price AS low,
                   close_price AS close, volume
            FROM market_data
            WHERE stock_code = %s
              AND trade_date >= %s
              AND trade_date <= %s
            ORDER BY trade_date ASC
        """
        df = pd.read_sql(query, pg_conn, params=(stock_code, start_date, end_date))
        df.rename(columns={'trade_date': 'datetime'}, inplace=True)
        return df

    @staticmethod
    def _generate_synthetic(start_date: str = None, end_date: str = None) -> pd.DataFrame:
        if start_date is None:
            start_date = '2024-01-01'
        if end_date is None:
            end_date = '2024-06-30'

        dates = pd.bdate_range(start=start_date, end=end_date)
        n = len(dates)
        np.random.seed(42)

        close = 10000.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.015, n)))
        open_p = np.concatenate([[close[0]], close[:-1]])
        high = np.maximum(open_p, close) * (1 + np.random.uniform(0, 0.02, n))
        low = np.minimum(open_p, close) * (1 - np.random.uniform(0, 0.02, n))
        volume = np.random.randint(100000, 5000000, n)

        df = pd.DataFrame({
            'datetime': dates,
            'open': open_p.astype(np.float64),
            'high': high.astype(np.float64),
            'low': low.astype(np.float64),
            'close': close.astype(np.float64),
            'volume': volume.astype(np.float64),
        })
        return df


class StrategyWrapper:
    def __init__(self, strategy_name: str, buy_signals: Dict, sell_signals: Dict):
        self.strategy_name = strategy_name
        self.buy_signals = self._normalize_keys(buy_signals)
        self.sell_signals = self._normalize_keys(sell_signals)

    @staticmethod
    def _normalize_keys(d: Dict) -> Dict:
        normalized = {}
        for k, v in d.items():
            if isinstance(k, str):
                normalized[k[:10]] = v
            elif isinstance(k, (date, datetime)):
                normalized[k.strftime('%Y-%m-%d')] = v
            else:
                normalized[str(k)[:10]] = v
        return normalized

    def get_strategy(self):
        buy_signals = self.buy_signals
        sell_signals = self.sell_signals

        class _SignalStrategy(bt.Strategy):
            params = (('name', self.strategy_name),)

            def next(self):
                dt = self.datas[0].datetime.date(0)
                dt_str = dt.strftime('%Y-%m-%d')
                if dt_str in buy_signals and buy_signals[dt_str]:
                    if not self.position:
                        self.buy()
                elif dt_str in sell_signals and sell_signals[dt_str]:
                    if self.position:
                        self.close()

        return _SignalStrategy


class FactorPortfolioStrategy:
    """Rank-based factor portfolio: on rebalance dates hold top-N equal-weight, close dropped names.

    rankings maps 'YYYY-MM-DD' -> ordered stock codes (rank 1 = best); the feed of each
    stock must carry its code in feed._name so the strategy can map datas to codes.
    """

    def __init__(self, strategy_name: str, rankings: Dict, top_n: int = 5):
        self.strategy_name = strategy_name
        self.rankings = self._normalize_keys(rankings)
        self.top_n = top_n

    @staticmethod
    def _normalize_keys(d: Dict) -> Dict:
        normalized = {}
        for k, v in d.items():
            if isinstance(k, str):
                normalized[k[:10]] = v
            elif isinstance(k, (date, datetime)):
                normalized[k.strftime('%Y-%m-%d')] = v
            else:
                normalized[str(k)[:10]] = v
        return normalized

    def get_strategy(self):
        rankings = self.rankings
        top_n = self.top_n

        class _FactorPortfolio(bt.Strategy):
            params = (('name', self.strategy_name),)

            def next(self):
                dt_str = self.datas[0].datetime.date(0).strftime('%Y-%m-%d')
                if dt_str not in rankings:
                    return
                targets = rankings[dt_str][:top_n]
                target_set = set(targets)
                for d in self.datas:
                    if self.getposition(d).size > 0 and d._name not in target_set:
                        self.close(data=d)
                if not targets:
                    return
                weight = 1.0 / len(targets)
                for d in self.datas:
                    if d._name in target_set:
                        self.order_target_percent(data=d, target=weight)

        return _FactorPortfolio


def create_cerebro(
    strategies: list,
    data_feeds: list,
    cash: float = 10000000,
    commission: float = 0.00015,
    slippage: float = 0.003,
) -> bt.Cerebro:
    cerebro = bt.Cerebro()
    for df in data_feeds:
        cerebro.adddata(df)
    for sw in strategies:
        cls = sw.get_strategy()
        cerebro.addstrategy(cls)
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=commission)
    if slippage:
        cerebro.broker.set_slippage_perc(slippage)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', riskfreerate=0.02, annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
    cerebro.addanalyzer(bt.analyzers.VWR, _name='vwr')
    return cerebro


def run_backtest(cerebro: bt.Cerebro, verbose: bool = False) -> Dict:
    if verbose:
        logger.setLevel(logging.INFO)
    results = cerebro.run()
    strat = results[0]
    metrics = _extract_analyzers(strat)
    metrics['final_value'] = cerebro.broker.getvalue()
    metrics['total_return_pct'] = ((metrics['final_value'] - cerebro.broker.startingcash) / cerebro.broker.startingcash) * 100
    return metrics


def _extract_analyzers(strat) -> Dict:
    metrics = {}

    sharpe = strat.analyzers.sharpe.get_analysis()
    metrics['sharpe_ratio'] = sharpe.get('sharperatio', None)

    drawdown = strat.analyzers.drawdown.get_analysis()
    metrics['max_drawdown'] = drawdown.get('max', {}).get('drawdown', 0.0)

    returns = strat.analyzers.returns.get_analysis()
    metrics['total_return'] = returns.get('rtot', 0.0)

    trade = strat.analyzers.trades.get_analysis()
    total = trade.get('total', {})
    won = total.get('won', 0)
    lost = total.get('lost', 0)
    metrics['num_trades'] = total.get('total', 0)
    metrics['win_rate'] = (won / (won + lost)) if (won + lost) > 0 else 0.0

    vwr = strat.analyzers.vwr.get_analysis()
    metrics['vwr'] = vwr.get('vwr', None)

    return metrics
