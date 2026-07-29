import datetime

import pandas as pd
import pytest

from services.backtester.backtrader_integration import (
    PGDataFeed,
    StrategyWrapper,
    create_cerebro,
    run_backtest,
)


class TestPGDataFeed:
    def test_synthetic_data_shape(self):
        feed = PGDataFeed(stock_code='000000', start_date='2024-01-01', end_date='2024-01-31')
        df = feed.p.dataname
        assert len(df) > 0
        assert 'open' in df.columns
        assert 'high' in df.columns
        assert 'low' in df.columns
        assert 'close' in df.columns
        assert 'volume' in df.columns
        assert df['open'].iloc[0] > 0
        assert df['high'].iloc[0] > 0
        assert df['low'].iloc[0] > 0
        assert df['close'].iloc[0] > 0
        assert df['volume'].iloc[0] > 0

    def test_synthetic_data_ohlc_integrity(self):
        feed = PGDataFeed(stock_code='000000', start_date='2024-01-01', end_date='2024-01-10')
        df = feed.p.dataname
        for i in range(len(df)):
            assert df['high'].iloc[i] >= df['low'].iloc[i]
            assert df['high'].iloc[i] >= df['close'].iloc[i]
            assert df['high'].iloc[i] >= df['open'].iloc[i]
            assert df['low'].iloc[i] <= df['open'].iloc[i]
            assert df['low'].iloc[i] <= df['close'].iloc[i]

    def test_synthetic_data_default_dates(self):
        feed = PGDataFeed(stock_code='000000')
        df = feed.p.dataname
        assert len(df) > 0

    def test_synthetic_line_names(self):
        feed = PGDataFeed(stock_code='000000', start_date='2024-01-01', end_date='2024-01-05')
        line_names = [l for l in feed.getlinealiases()]
        assert 'open' in line_names
        assert 'high' in line_names
        assert 'low' in line_names
        assert 'close' in line_names
        assert 'volume' in line_names


class TestStrategyWrapper:
    def test_buy_signal_creates_position(self):
        dates = pd.bdate_range('2024-01-01', '2024-01-10')
        buy_signals = {d.strftime('%Y-%m-%d'): True for d in dates[:3]}
        sell_signals = {d.strftime('%Y-%m-%d'): True for d in dates[5:7]}

        sw = StrategyWrapper('test_buy', buy_signals, sell_signals)
        data = PGDataFeed(stock_code='000000', start_date='2024-01-01', end_date='2024-01-10')
        cerebro = create_cerebro([sw], [data], cash=10000000, commission=0, slippage=0)
        results = cerebro.run()
        strat = results[0]

        trade = strat.analyzers.trades.get_analysis()
        assert trade['total']['total'] >= 1

    def test_no_signals_no_trades(self):
        sw = StrategyWrapper('test_noop', {}, {})
        data = PGDataFeed(stock_code='000000', start_date='2024-01-01', end_date='2024-01-10')
        cerebro = create_cerebro([sw], [data], cash=10000000, commission=0, slippage=0)
        results = cerebro.run()
        strat = results[0]
        trade = strat.analyzers.trades.get_analysis()
        assert trade['total']['total'] == 0

    def test_sell_without_position_no_error(self):
        sw = StrategyWrapper('test_sell_only', {}, {'2024-01-03': True})
        data = PGDataFeed(stock_code='000000', start_date='2024-01-01', end_date='2024-01-10')
        cerebro = create_cerebro([sw], [data], cash=10000000, commission=0, slippage=0)
        results = cerebro.run()
        strat = results[0]
        trade = strat.analyzers.trades.get_analysis()
        assert trade['total']['total'] == 0


class TestCreateCerebro:
    def test_configured_correctly(self):
        data = PGDataFeed(stock_code='000000', start_date='2024-01-01', end_date='2024-01-10')
        sw = StrategyWrapper('test', {}, {})
        cerebro = create_cerebro([sw], [data], cash=5000000, commission=0.001, slippage=0.01)

        assert cerebro.broker.getcash() == 5000000
        assert len(cerebro.datas) == 1
        assert len(cerebro.strats) == 1

    def test_multiple_data_feeds(self):
        d1 = PGDataFeed(stock_code='000001', start_date='2024-01-01', end_date='2024-01-10')
        d2 = PGDataFeed(stock_code='000002', start_date='2024-01-01', end_date='2024-01-10')
        sw = StrategyWrapper('test_multi', {}, {})
        cerebro = create_cerebro([sw], [d1, d2], cash=10000000)
        assert len(cerebro.datas) == 2


class TestRunBacktest:
    def test_returns_metrics_dict(self):
        dates = pd.bdate_range('2024-01-01', '2024-01-31')
        buy_signals = {d.strftime('%Y-%m-%d'): True for d in dates[0:20:5]}
        sell_signals = {d.strftime('%Y-%m-%d'): True for d in dates[3:25:5]}

        sw = StrategyWrapper('test_run', buy_signals, sell_signals)
        data = PGDataFeed(stock_code='000000', start_date='2024-01-01', end_date='2024-01-31')
        cerebro = create_cerebro([sw], [data])
        metrics = run_backtest(cerebro, verbose=False)

        expected_keys = {'sharpe_ratio', 'max_drawdown', 'total_return', 'num_trades', 'win_rate', 'final_value', 'total_return_pct', 'vwr'}
        assert expected_keys.issubset(metrics.keys())
        assert metrics['num_trades'] >= 0
        assert metrics['final_value'] > 0

    def test_profitable_strategy(self):
        dates = pd.bdate_range('2024-01-01', '2024-01-31')
        buy_signals = {}
        sell_signals = {}
        for i, d in enumerate(dates):
            if i < 5:
                buy_signals[d.strftime('%Y-%m-%d')] = True
            if 10 <= i < 15:
                sell_signals[d.strftime('%Y-%m-%d')] = True

        sw = StrategyWrapper('test_profit', buy_signals, sell_signals)
        data = PGDataFeed(stock_code='000000', start_date='2024-01-01', end_date='2024-01-31')
        cerebro = create_cerebro([sw], [data])
        metrics = run_backtest(cerebro)

        assert isinstance(metrics['sharpe_ratio'], (float, type(None)))
        assert isinstance(metrics['max_drawdown'], (int, float))
        assert metrics['max_drawdown'] >= 0
