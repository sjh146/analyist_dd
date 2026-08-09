#!/usr/bin/env python3
"""factor_backtest.py — point-in-time factor portfolio backtest via backtrader.

강환국『하면 된다! 퀀트투자』 팩터 규칙을 analyist_dd의 backtrader 통합 계층
(backtrader_integration.py) 위에서 백테스트한다. 리밸런싱일마다 유니버스
(T2 universe.filter_universe) + 팩터(T4~T8: value/quality/momentum/lowvol/
multifactor)로 상위 N을 선택해 ``FactorPortfolioStrategy``(동일가중, 이탈 매도)로
실행한다.

DB 없이도 동작: ``pg_conn``을 지정하지 않으면 내장 ``MockStorage``(재무·가격이
기간에 따라 변하는 N종목)를 사용한다 — 실스키마/모의 유니버스 모두 point-in-time
규칙을 준수한다. 실매매 없음: 순수 백테스트.

Usage:
  python scripts/factor_backtest.py --strategy value_factor --top-n 5 \
      --start 2024-01-01 --end 2024-06-30 [--rebalance 63] [--universe 12]

책 규칙(하드코딩, 튜닝 금지 — 과최적화 방지) vs 본 스크립트:
+---------------+-----------------------------------+------------------------------+
| 항목          | 책 규칙                            | 본 백테스트                   |
+---------------+-----------------------------------+------------------------------+
| 종목 선정     | 유니버스 내 팩터 랭크 상위 30 동일가중 | --top-n(스모크 5) 동일가중   |
| 리밸런싱      | 재무 분기 / 모멘텀 월간             | --rebalance (기본 63 / 21)    |
| 가치          | 저PER·저PBR 결합                     | value_ratios.compute_ratios   |
| 퀄리티        | ROE 2년 평균·GP/A·이익안정성         | quality_strategy              |
| 모멘텀        | 12-1·3-6·52주 고가 근접              | momentum_strategy(252/21/63)  |
| 저변동        | 252일 변동성·베타 (long-only)        | lowvol_strategy               |
| 멀티팩터      | Z-score 동일가중 상위 20             | multifactor_strategy          |
+---------------+-----------------------------------+------------------------------+

⚠️ 과최적화 금지: 위 임계값은 임의로 바꾸지 않는다. mock은 DB 부재 환경의 스모크
용이며, ``stocks.market_cap``은 현재가 기준(계획서 P2-1: 경미한 look-ahead, 후속
이슈로 기록)이다.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# ── sys.path bootstrap ──────────────────────────────────────────────────────
# 스크립트는 services/backtester/scripts/ 에서 실행된다. 백테스트 통합 계층은
# services.backtester, 팩터 모듈(T2~T8)은 services/strategy-agents 의 app.* 이다.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKTESTER_DIR = os.path.dirname(_SCRIPT_DIR)
_STRATEGY_AGENTS_DIR = os.path.abspath(os.path.join(_BACKTESTER_DIR, "..", "strategy-agents"))
_REPO_ROOT = os.path.abspath(os.path.join(_BACKTESTER_DIR, "..", ".."))
for _p in (_REPO_ROOT, _STRATEGY_AGENTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ⚠️ `services.backtester` 패키지를 import하면 __init__ → runner.py 가 실행되어
# sys.path[0] 에 xgboost-ml 을 삽입하고 `app`(공용 최상위 패키지명)이 xgboost-ml/app
# 으로 고정된다(sys.modules 캐시). 팩터 모듈(strategy-agents/app)과 충돌하므로
# backtrader_integration.py 를 패키지 경유 없이 독립 모듈로 로드한다.
import importlib.util  # noqa: E402

_bti_path = os.path.join(_BACKTESTER_DIR, "backtrader_integration.py")
_bti_spec = importlib.util.spec_from_file_location("backtrader_integration", _bti_path)
_bti = importlib.util.module_from_spec(_bti_spec)
sys.modules[_bti_spec.name] = _bti  # backtrader 메타클래스가 cls.__module__ 을 sys.modules 에서 조회
_bti_spec.loader.exec_module(_bti)
FactorPortfolioStrategy = _bti.FactorPortfolioStrategy
PGDataFeed = _bti.PGDataFeed
create_cerebro = _bti.create_cerebro
run_backtest = _bti.run_backtest

from app.factors.financial_snapshot import FinancialSnapshot  # noqa: E402
from app.factors.universe import filter_universe  # noqa: E402
from app.strategies.lowvol_strategy import LowVolatilityStrategy  # noqa: E402
from app.strategies.momentum_strategy import MomentumStrategy  # noqa: E402
from app.strategies.multifactor_strategy import MultiFactorStrategy  # noqa: E402
from app.strategies.quality_strategy import QualityStrategy  # noqa: E402
from app.strategies.value_strategy import ValueStrategy  # noqa: E402

# 전략 이름 → 팩터 전략 클래스 (T5~T9). 재무 기반 전략은 분기(63일), 가격 기반
# 전략은 월간(21일) 리밸런싱 — 계획서 심층분석 5의 실행 타이밍 규칙.
STRATEGY_CLASSES = {
    "value_factor": ValueStrategy,
    "quality_factor": QualityStrategy,
    "momentum_factor": MomentumStrategy,
    "lowvol_factor": LowVolatilityStrategy,
    "multifactor": MultiFactorStrategy,
}
DEFAULT_REBALANCE = {
    "value_factor": 63,
    "quality_factor": 63,
    "momentum_factor": 21,
    "lowvol_factor": 21,
    "multifactor": 21,
}

_QUARTER_ENDS = [
    "2022-06-30", "2022-09-30", "2022-12-31",
    "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
    "2024-03-31",
]


class MockStorage:
    """DB 없이 동작하는 스토리지 스텁 — 팩터 모듈(T1/T2)의 읽기 계약만 구현.

    재무(분기별)와 가격(일별)이 기간에 따라 변하도록 설계해 리밸런싱 사이 랭킹이
    실제로 바뀐다. point-in-time 규칙(``report_date <= asof``)은 ``get_financial_
    statements``/``get_price_series_asof``에서 그대로 지킨다.
    """

    def __init__(self, n_stocks: int = 12, seed: int = 42):
        self.n = n_stocks
        self.seed = seed
        self._codes = [f"MF{i:04d}" for i in range(n_stocks)]
        self._prices = self._build_prices()
        self._financials = self._build_financials()

    # ── data construction ──────────────────────────────────────────────────
    def _build_financials(self) -> Dict[str, List[Dict]]:
        """분기별 재무: 종목·분기마다 다른 순이익 성장 → 가치/퀄리티 랭킹이 변동."""
        rows: Dict[str, List[Dict]] = {}
        for i, code in enumerate(self._codes):
            cap = 2e12 + i * 3e11                       # 시총 (stocks.market_cap)
            revenue = 5e11 + i * 4e10
            equity = 8e11 + i * 2e10
            assets = 2e12 + i * 3e10
            gprofit = revenue * (0.18 + 0.01 * (i % 4))
            stock_rows = []
            for qi, q in enumerate(_QUARTER_ENDS):
                # 분기별 성장률이 종목마다 달라 PER/PBR 랭크가 분기마다 재배열된다.
                growth = (1.0 + 0.04 * qi) * (1.45 if (i + qi) % 2 == 0 else 0.72)
                ni = (2e10 + i * 3e9) * growth
                stock_rows.append({
                    "stock_code": code,
                    "report_date": q,
                    "net_income": ni,
                    "total_equity": equity,
                    "revenue": revenue,
                    "total_assets": assets,
                    "gross_profit": gprofit,
                })
            rows[code] = stock_rows
        return rows

    def _build_prices(self) -> Dict[str, List[float]]:
        """일별 종가: 2022-07-01~2024-06-30, 연도별 드리프트가 달라 모멘텀 랭킹 변동."""
        dates = pd.bdate_range("2022-07-01", "2024-06-30")
        prices: Dict[str, List[float]] = {}
        for i, code in enumerate(self._codes):
            rng = np.random.default_rng(self.seed + i)
            mu_2023 = 0.0004 + 0.00025 * (i % 5)          # 2023년 드리프트 순위
            mu_2024 = 0.0004 + 0.00025 * ((i + 3) % 5)     # 2024년엔 순위 재배열
            sigma = 0.010 + 0.003 * (i % 4)                # 저변동 랭킹용 변동성 차이
            log_ret = []
            for d in dates:
                mu = mu_2023 if d.year == 2023 else mu_2024
                log_ret.append(rng.normal(mu, sigma))
            base = 10000.0 + 1500.0 * (i % 7)
            series = [base]
            for r in log_ret:
                series.append(series[-1] * np.exp(r))
            prices[code] = series
        return prices

    # ── storage read contract (T1/T2) ──────────────────────────────────────
    def get_strategy_config(self, strategy_name: str) -> Optional[Dict]:
        return None  # 임계값은 코드 상수 — DB/모의 config에 튜닝 파라미터 없음

    def get_all_stocks(self, limit: Optional[int] = None) -> List[Dict]:
        stocks = [
            {"stock_code": c, "stock_name": c, "sector": "MOCK", "market": "KOSPI", "market_cap": 2e12 + i * 3e11}
            for i, c in enumerate(self._codes)
        ]
        return stocks[:limit] if limit else stocks

    def get_market_caps(self) -> Dict[str, Optional[float]]:
        return {c: 2e12 + i * 3e11 for i, c in enumerate(self._codes)}

    def get_avg_trading_value(self, stock_code: str, days: int = 30) -> Optional[float]:
        return 5e9  # 유니버스 하한(10억) 충족

    def get_avg_trading_value_asof(self, stock_code: str, days: int = 30, asof_date=None) -> Optional[float]:
        return 5e9

    def get_first_trade_date(self, stock_code: str) -> Optional[str]:
        return "2022-07-01"  # 상장 1년 이상 (asof - 252일 이전)

    def get_financial_statements(self, stock_code: str, asof_date=None) -> List[Dict]:
        """point-in-time: report_date <= asof_date 만 반환 (선견 편향 방지)."""
        rows = self._financials.get(stock_code, [])
        if asof_date is not None:
            asof = str(asof_date)[:10]
            rows = [r for r in rows if r["report_date"] <= asof]
        return rows

    def get_latest_financials(self, stock_code: str) -> Optional[Dict]:
        rows = self.get_financial_statements(stock_code)
        return rows[-1] if rows else None

    def get_price_series_asof(self, stock_code: str, days: int = 60, asof_date=None) -> List[float]:
        """point-in-time: trade_date <= asof_date 의 종가 시리즈 (오름차순)."""
        series = self._prices.get(stock_code, [])
        if asof_date is not None:
            asof = pd.Timestamp(asof_date)
            dates = pd.bdate_range("2022-07-01", "2024-06-30")
            series = [p for d, p in zip(dates, series) if d <= asof]
        return series[-days:]

    def get_price_series(self, stock_code: str, days: int = 60) -> List[float]:
        return self.get_price_series_asof(stock_code, days=days, asof_date=None)

    def get_positions(self) -> List[Dict]:
        return []  # 백테스트는 포지션을 스스로 관리 — 실계좌 포지션 미조회


def rebalance_dates(start: str, end: str, interval_days: int) -> List[date]:
    """start..end 사이 영업일 기준 interval_days 간격의 리밸런싱 날짜 목록."""
    bdates = pd.bdate_range(start=start, end=end)
    if len(bdates) == 0:
        return []
    dates: List[date] = []
    d = bdates[0]
    while d.date() <= pd.Timestamp(end).date():
        dates.append(d.date())
        d = d + pd.offsets.BDay(interval_days)
    return dates


def _compute_rankings(
    strategy_cls,
    storage,
    stocks: List[Dict],
    rebalance_days: List[date],
    top_n: int,
) -> Dict[str, List[str]]:
    """리밸런싱일마다 유니버스(T2) + 팩터(T4~T8)로 상위 N을 뽑는다.

    Returns { 'YYYY-MM-DD': [rank1..rankN] } — 랭크 1 = best.
    유니버스 0개인 날짜는 생략(에러 아님).
    """
    strategy = strategy_cls(storage)
    rankings: Dict[str, List[str]] = {}
    for asof in rebalance_days:
        universe = filter_universe(storage, stocks, asof_date=asof)
        if not universe:
            continue
        caps = storage.get_market_caps()
        cap_map = {code: caps.get(code) for code in universe}
        snapshot = FinancialSnapshot(storage)
        scores = strategy._factor_scores(universe, snapshot, asof, cap_map)
        ranked = strategy._rank(scores)
        ranked_codes = sorted(ranked, key=ranked.get)
        if ranked_codes:
            rankings[asof.isoformat()] = ranked_codes[:top_n]
    return rankings


def _rankings_changed(rankings: Dict[str, List[str]]) -> bool:
    """리밸런싱 사이 랭킹이 한 번이라도 바뀌었는지 (mock 설계 검증용)."""
    keys = sorted(rankings)
    return any(rankings[keys[i]] != rankings[keys[i - 1]] for i in range(1, len(keys)))


def run_factor_backtest(
    strategy: str,
    top_n: int,
    start: str,
    end: str,
    rebalance: Optional[int] = None,
    n_universe: int = 12,
    seed: int = 42,
    output: Optional[str] = None,
) -> Dict:
    """팩터 백테스트 실행 — 결과 dict 반환, output 지정 시 JSON 파일로 기록."""
    if strategy not in STRATEGY_CLASSES:
        raise ValueError(f"unknown strategy: {strategy} (use one of {sorted(STRATEGY_CLASSES)})")
    strategy_cls = STRATEGY_CLASSES[strategy]
    interval = rebalance or DEFAULT_REBALANCE[strategy]

    storage = MockStorage(n_stocks=n_universe, seed=seed)
    stocks = storage.get_all_stocks()
    days = rebalance_dates(start, end, interval)

    rankings = _compute_rankings(strategy_cls, storage, stocks, days, top_n)

    # 유니버스 0개 → 빈 결과(에러 아님). 백트레이더는 데이터피드 없이 실행 불가이므로 사전 반환.
    if not rankings:
        result = {
            "strategy": strategy,
            "top_n": top_n,
            "rebalance_days": interval,
            "start": start,
            "end": end,
            "mode": "mock",
            "seed": seed,
            "status": "empty_universe",
            "universe_size": 0,
            "rebalance_dates": [d.isoformat() for d in days],
            "rankings_changed": False,
            "metrics": {
                "total_return": 0.0,
                "sharpe": None,
                "sharpe_ratio": None,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "num_trades": 0,
                "final_value": 0.0,
                "total_return_pct": 0.0,
            },
        }
        if output:
            os.makedirs(os.path.dirname(output), exist_ok=True)
            with open(output, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        return result

    # 랭킹에 등장한 종목 전체를 데이터피드로 구성 (name=종목코드 — FactorPortfolioStrategy
    # 가 d._name 으로 데이터를 매핑한다).
    feed_codes = []
    for ranked in rankings.values():
        for code in ranked:
            if code not in feed_codes:
                feed_codes.append(code)

    feeds = [
        PGDataFeed(stock_code=code, start_date=start, end_date=end, name=code)
        for code in feed_codes
    ]
    portfolio = FactorPortfolioStrategy(strategy, rankings, top_n=top_n)
    cerebro = create_cerebro([portfolio], feeds, cash=10000000)
    metrics = run_backtest(cerebro)

    result = {
        "strategy": strategy,
        "top_n": top_n,
        "rebalance_days": interval,
        "start": start,
        "end": end,
        "mode": "mock",
        "seed": seed,
        "status": "ok",
        "universe_size": n_universe,
        "rebalance_dates": sorted(rankings),
        "rankings_changed": _rankings_changed(rankings),
        "metrics": {
            "total_return": round(float(metrics.get("total_return", 0.0)), 6),
            "sharpe": metrics.get("sharpe_ratio"),
            "sharpe_ratio": metrics.get("sharpe_ratio"),
            "max_drawdown": round(float(metrics.get("max_drawdown", 0.0)), 6),
            "win_rate": round(float(metrics.get("win_rate", 0.0)), 6),
            "num_trades": int(metrics.get("num_trades", 0)),
            "final_value": round(float(metrics.get("final_value", 0.0)), 2),
            "total_return_pct": round(float(metrics.get("total_return_pct", 0.0)), 6),
        },
        "book_rules": (
            "책(하면 된다! 퀀트투자) 규칙 하드코딩 준수 — 상위 N 동일가중·12-1/3-6/52주·"
            "252일 변동성·Z-score 동일가중. 임계값 튜닝 없음(과최적화 방지)."
        ),
        "note": (
            "DB 부재 환경 스모크 → 내장 MockStorage 사용. stocks.market_cap 은 현재가 기준 "
            "(계획서 P2-1: 경미한 look-ahead, 후속 이슈). 리밸런싱/유니버스 차이로 책 지표와 "
            "직접 비교 불가 — 재검증 목적."
        ),
    }

    if output:
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    return result


def default_output_path(strategy: str) -> str:
    return os.path.join(_REPO_ROOT, ".omo", "evidence", f"factor-backtest-{strategy}.json")


def main(argv: Optional[List[str]] = None) -> Dict:
    parser = argparse.ArgumentParser(
        description="Point-in-time factor portfolio backtest (backtrader, DB-optional).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--strategy", required=True, choices=sorted(STRATEGY_CLASSES),
                        help="factor strategy to backtest (T4~T8)")
    parser.add_argument("--top-n", type=int, default=5, help="top-N names held per rebalance")
    parser.add_argument("--start", default="2024-01-01", help="backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-06-30", help="backtest end date (YYYY-MM-DD)")
    parser.add_argument("--rebalance", type=int, default=None,
                        help="rebalance interval in business days (default: 63 financial / 21 price-based)")
    parser.add_argument("--universe", type=int, default=12, help="mock universe size (n stocks)")
    parser.add_argument("--seed", type=int, default=42, help="mock data RNG seed")
    parser.add_argument("--output", default=None,
                        help="output JSON path (default: .omo/evidence/factor-backtest-<strategy>.json)")
    args = parser.parse_args(argv)

    output = args.output or default_output_path(args.strategy)
    result = run_factor_backtest(
        strategy=args.strategy,
        top_n=args.top_n,
        start=args.start,
        end=args.end,
        rebalance=args.rebalance,
        n_universe=args.universe,
        seed=args.seed,
        output=output,
    )

    m = result["metrics"]
    print(f"[factor-backtest] strategy={result['strategy']} "
          f"status={result['status']} universe={result['universe_size']} "
          f"rebalances={len(result['rebalance_dates'])} rankings_changed={result['rankings_changed']}")
    print(f"  total_return={m['total_return']} sharpe={m['sharpe']} "
          f"max_drawdown={m['max_drawdown']} win_rate={m['win_rate']} num_trades={m['num_trades']}")
    print(f"  output -> {output}")
    return result


if __name__ == "__main__":
    main()
