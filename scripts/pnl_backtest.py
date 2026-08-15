"""P&L 기반 백테스트 — 수수료/슬리피지/거래세 포함 스윙 매매 시뮬레이션.

전략: 매일 챔피언 예측 상위 K종목(conf >= threshold)을 동일 비중으로 진입,
H일 보유 후 청산. 비용 모델: 매수 수수료 + 매도 수수료 + 거래세 + 슬리피지.
메트릭: total_return, CAGR, MDD, 샤프, 승률, 거래 수 → strategy_runs 기록.

2026-08 신규. 데이터 제약: 일봉(종가)만 있으므로 t일 예측 → t일 종가 진입
(슬리피지로 현실화). 롱온리 기본, --short 옵션으로 롱숏 가능.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, '/app')

# 비용 모델 (한국 주식)
FEE_RATE = 0.00015          # 수수료 0.015% (매수/매도 각각)
TAX_RATE = 0.0018           # 거래세 0.18% (매도)
SLIPPAGE = 0.0005           # 슬리피지 0.05% (진입/청산 각각)


def apply_buy_cost(price: float) -> float:
    """매수 비용 반영 (수수료 + 슬리피지) — 실제 매수 단가."""
    return price * (1.0 + FEE_RATE + SLIPPAGE)


def apply_sell_cost(price: float) -> float:
    """매도 비용 반영 (수수료 + 거래세 + 슬리피지) — 실제 매도 단가."""
    return price * (1.0 - FEE_RATE - TAX_RATE - SLIPPAGE)


def compute_metrics(equity_curve: np.ndarray, trade_returns: list) -> dict:
    """equity_curve(일별 자산)와 종목별 수익률로 MDD/샤프/승률 계산."""
    eq = np.asarray(equity_curve, dtype=float)
    if len(eq) < 2:
        return {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0,
                "sharpe": 0.0, "win_rate": 0.0, "n_trades": 0}
    total_return = eq[-1] / eq[0] - 1.0
    days = len(eq)
    cagr = (eq[-1] / eq[0]) ** (252.0 / max(days, 1)) - 1.0 if eq[0] > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    drawdowns = (eq - peak) / peak
    mdd = float(np.min(drawdowns)) if len(drawdowns) else 0.0
    daily_ret = np.diff(eq) / eq[:-1]
    sharpe = float(np.mean(daily_ret) / np.std(daily_ret) * np.sqrt(252)) if np.std(daily_ret) > 0 else 0.0
    wins = [r for r in trade_returns if r > 0]
    win_rate = len(wins) / len(trade_returns) if trade_returns else 0.0
    return {
        "total_return": round(float(total_return), 4),
        "cagr": round(float(cagr), 4),
        "max_drawdown": round(mdd, 4),
        "sharpe": round(sharpe, 3),
        "win_rate": round(float(win_rate), 3),
        "n_trades": len(trade_returns),
    }


def simulate(df, date_col, price_col, k=5, hold_days=5, threshold=0.55,
             initial_cash=1_000_000.0, short=False):
    """매일 top-K 진입(H일 보유) 롱온리/롱숏 시뮬레이션."""
    dates = sorted(df[date_col].unique())
    if not len(dates):
        return compute_metrics(np.array([initial_cash]), [])
    price_by_date = {d: df[df[date_col] == d].set_index('stock_code')[price_col].to_dict()
                     for d in dates}
    prob_by_date = {d: df[df[date_col] == d].set_index('stock_code')['_prob'].to_dict()
                    for d in dates}

    cash = initial_cash
    positions = []  # {code, entry_date, entry_price, qty, side}
    equity_curve = []
    trade_returns = []
    last_px = {}  # 종목별 마지막 유효 가격 (휴장일 forward-fill 평가용)

    for i, d in enumerate(dates):
        # 1) 청산: H일 지난 포지션 매도
        for pos in [p for p in positions if (dates.index(p['entry_date']) <= i - hold_days)]:
            px = price_by_date[d].get(pos['code'])
            if px is None or px <= 0:
                continue  # 가격 없음(휴장일) — 다음날 매도 재시도
            if pos['side'] == 'long':
                sell_px = apply_sell_cost(px)
                cash += pos['qty'] * sell_px
                ret = sell_px / pos['entry_price'] - 1.0
            else:  # short
                buy_px = apply_buy_cost(px)
                cash += pos['qty'] * (2 * pos['entry_price'] - buy_px)
                ret = pos['entry_price'] / buy_px - 1.0
            trade_returns.append(ret)
            positions.remove(pos)

        # 2) 진입: top-K (신규 진입만)
        probs_today = prob_by_date.get(d, {})
        cands = [(c, p) for c, p in probs_today.items() if p >= threshold]
        cands.sort(key=lambda x: x[1], reverse=True)
        held_codes = {p['code'] for p in positions}
        fresh = [c for c, _ in cands if c not in held_codes][:k]
        if fresh and price_by_date.get(d):
            alloc = cash / max(len(fresh), 1)
            for code in fresh:
                px = price_by_date[d].get(code)
                if px is None or px <= 0:
                    continue
                entry_px = apply_buy_cost(px)
                qty = int(alloc / entry_px) if entry_px > 0 else 0
                if qty <= 0:
                    continue
                cost = qty * entry_px
                if cost > cash:
                    continue
                cash -= cost
                positions.append({'code': code, 'entry_date': d,
                                  'entry_price': entry_px, 'qty': qty,
                                  'side': 'short' if short else 'long'})

        # 3) 자산 평가
        mv = cash
        for pos in positions:
            px = price_by_date.get(d, {}).get(pos['code'])
            # 2026-08: 휴장일/데이터 결측 시 0으로 평가하면 MDD -99% 폭락 —
            # 마지막 유효 가격(forward-fill), 없으면 진입가로 평가
            if px is None or px <= 0:
                px = last_px.get(pos['code'])
            if px is None or px <= 0:
                px = pos['entry_price']
            last_px[pos['code']] = px
            if pos['side'] == 'long':
                mv += pos['qty'] * px
            else:
                mv += pos['qty'] * (2 * pos['entry_price'] - px)
        equity_curve.append(mv)

    # 마지막 날 강제 청산 (미청산 포지션)
    last_d = dates[-1]
    for pos in positions:
        px = price_by_date[last_d].get(pos['code'])
        if px is None:
            continue
        if pos['side'] == 'long':
            cash += pos['qty'] * apply_sell_cost(px)
            ret = apply_sell_cost(px) / pos['entry_price'] - 1.0
        else:
            cash += pos['qty'] * (2 * pos['entry_price'] - apply_buy_cost(px))
            ret = pos['entry_price'] / apply_buy_cost(px) - 1.0
        trade_returns.append(ret)

    metrics = compute_metrics(equity_curve, trade_returns)
    metrics['final_equity'] = round(float(equity_curve[-1]), 2)
    # 최저점 진단 (2026-08 MDD -99% 버그 추적용) + equity 곡선 (대시보드용)
    eq = np.asarray(equity_curve, dtype=float)
    if len(eq) > 2:
        trough_i = int(np.argmin(eq))
        metrics['equity_curve'] = [round(float(v), 2) for v in eq]
        metrics['_debug'] = {
            "trough_day": str(dates[trough_i]),
            "trough_equity": round(float(eq[trough_i]), 2),
            "peak_before": round(float(np.max(eq[:trough_i + 1])), 2),
            "n_days": len(eq),
        }
    return metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=90)
    ap.add_argument('--k', type=int, default=5, help='동시 보유 최대 종목 수')
    ap.add_argument('--hold', type=int, default=5, help='보유 일수')
    ap.add_argument('--threshold', type=float, default=0.55)
    ap.add_argument('--short', action='store_true', help='롱숏 (기본 롱온리)')
    ap.add_argument('--scenarios', action='store_true',
                    help='파라미터 그리드 비교 모드 (패널 1회 빌드로 여러 전략)')
    ap.add_argument('--n-kospi', type=int, default=30)
    ap.add_argument('--n-kosdaq', type=int, default=20)
    args = ap.parse_args()

    import psycopg2
    from app.feature_engine.feature_pipeline import FeaturePipeline
    from app.models.ensemble_model import EnsembleModel
    from app.training.universe import select_backtest_universe

    pg = psycopg2.connect(host='postgres', port=5432, dbname='stock_trading',
                          user='stock_user',
                          password=os.environ.get('POSTGRES_PASSWORD', ''))
    stocks = select_backtest_universe(pg, n_kospi=args.n_kospi, n_kosdaq=args.n_kosdaq,
                                      min_days=30, seed=42)
    print(f'universe: {len(stocks)} stocks')

    pipeline = FeaturePipeline(pg_conn=pg)
    end = datetime.now()
    start = end - timedelta(days=args.days)
    df = pipeline.build_training_features(
        stocks, start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')
    )
    if df is None or len(df) < 200:
        print('FAILED - insufficient data')
        sys.exit(1)

    # 챔피언 계약 행렬 (json 순서 0-fill)
    ensemble = EnsembleModel(model_dir='app/models/champion')
    ensemble.load('app/models/champion')
    model_f = ensemble.load_feature_names('app/models/champion')
    X = np.zeros((len(df), len(model_f)), dtype=np.float32)
    for j, f in enumerate(model_f):
        if f in df.columns:
            X[:, j] = np.nan_to_num(df[f].to_numpy(dtype=np.float64),
                                    nan=0.0, posinf=0.0, neginf=0.0)
    df = df.reset_index(drop=True)
    df['_prob'] = ensemble.predict(X)

    date_col = 'date' if 'date' in df.columns else 'trade_date'
    price_col = 'price' if 'price' in df.columns else ('close' if 'close' in df.columns else 'close_price')
    mode = 'short' if args.short else 'long'

    if args.scenarios:
        # 시나리오 그리드: (threshold, k, hold) — 패널 1회 빌드로 여러 전략 비교
        grid = [
            (0.50, 5, 5), (0.50, 5, 10), (0.50, 3, 5),
            (0.53, 5, 5), (0.55, 5, 5), (0.55, 3, 10),
        ]
        print(f"[P&L scenarios] days={args.days} n_stocks={len(stocks)}")
        print(f"{'thr':>5} {'k':>2} {'hold':>4} {'ret':>8} {'cagr':>7} {'mdd':>8} {'sharpe':>6} {'win':>5} {'n':>3}")
        for thr, k, hold in grid:
            m = simulate(df, date_col, price_col, k=k, hold_days=hold,
                         threshold=thr, short=args.short)
            print(f"{thr:>5.2f} {k:>2} {hold:>4} {m['total_return']*100:>7.2f}% "
                  f"{m['cagr']*100:>6.1f}% {m['max_drawdown']*100:>7.2f}% "
                  f"{m['sharpe']:>6.2f} {m['win_rate']*100:>4.0f}% {m['n_trades']:>3}")
            try:
                sys.path.insert(0, '/app/scripts')
                from record_strategy_run import record_run
                record_run(
                    tool='backtest_pnl',
                    stocks=len(stocks),
                    metric_value=m['total_return'],
                    meta={"mode": mode, "k": k, "hold_days": hold, "threshold": thr,
                          "cagr": m['cagr'], "sharpe": m['sharpe'],
                          "max_drawdown": m['max_drawdown'],
                          "win_rate": m['win_rate'], "n_trades": m['n_trades'],
                          "scenario": True},
                )
            except Exception as e:
                print(f'[record_run] 시나리오 기록 실패(무시): {e}')
        return

    metrics = simulate(df, date_col, price_col, k=args.k,
                       hold_days=args.hold, threshold=args.threshold, short=args.short)
    print(f"[P&L backtest] mode={mode} k={args.k} hold={args.hold}d "
          f"threshold={args.threshold}")
    for key, val in metrics.items():
        if key in ("equity_curve", "_debug"):
            continue
        print(f"  {key}: {val}")
    if "_debug" in metrics:
        print(f"  [진단] 최저점 {metrics['_debug']}")

    # 저장 + strategy_runs 기록 (Grafana 대시보드용)
    result = {"tool": "backtest_pnl", "mode": mode, "k": args.k,
              "hold_days": args.hold, "threshold": args.threshold,
              "n_stocks": len(stocks), **metrics}
    os.makedirs('/app/reports', exist_ok=True)
    with open('/app/reports/pnl_backtest_result.json', 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    try:
        sys.path.insert(0, '/app/scripts')
        from record_strategy_run import record_run
        record_run(
            tool='backtest_pnl',
            stocks=len(stocks),
            metric_value=metrics['total_return'],
            meta={"mode": mode, "k": args.k, "hold_days": args.hold,
                  "threshold": args.threshold, "cagr": metrics['cagr'],
                  "sharpe": metrics['sharpe'], "max_drawdown": metrics['max_drawdown'],
                  "win_rate": metrics['win_rate'], "n_trades": metrics['n_trades']},
        )
    except Exception as e:
        print(f'[record_run] backtest_pnl 기록 실패(무시): {e}')
    pg.close()


if __name__ == '__main__':
    main()
