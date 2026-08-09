"""
lowvol_strategy - LowVolatilityStrategy (저변동성 전략, 강환국『하면 된다! 퀀트투자』).

일별 로그수익률 기반 두 팩터 (각각 낮을수록 좋음):
① 252일 연표준편차 = 일별 로그수익률 std × √252
② 베타 = cov(개별수익률, 시장수익률) / var(시장수익률) — 음수 베타 종목 제외

시장 대용: config `market_index_code`(기본 "000001" KRX KOSPI)의 가격 시리즈가
있으면 그 수익률 사용, 없으면 유니버스 일평균 수익률로 대체.
long-only: 공매도 금지, sell은 long 종료만. point-in-time 시리즈로 look-ahead bias 없음.
"""

import math
from typing import Dict, List, Optional

from app.factors.factor_base import rank_scores
from app.strategies.factor_strategy_base import FactorStrategyBase

_ONE_YEAR = 252
_DAYS = _ONE_YEAR + 1


class LowVolatilityStrategy(FactorStrategyBase):
    def __init__(self, storage, config: Optional[Dict] = None):
        super().__init__("lowvol_factor", storage, config)
        self.market_index_code = self.config.get("market_index_code", "000001")

    def _factor_scores(self, universe, snapshot, asof_date, cap_map) -> Dict[str, Optional[float]]:
        returns = {}
        for code in universe:
            series = self.storage.get_price_series_asof(code, days=_DAYS, asof_date=asof_date)
            ret = self._log_returns(series)[-_ONE_YEAR:]
            if ret:
                returns[code] = ret

        market = self._market_returns(returns, asof_date)
        if not market:
            return {}

        vols = {c: self._annualized_vol(r) for c, r in returns.items()}
        betas = {}
        for code, ret in returns.items():
            beta = self._beta(ret, market)
            if beta is not None and beta >= 0:
                betas[code] = beta

        vol_rank = rank_scores(vols, ascending=True)
        beta_rank = rank_scores(betas, ascending=True)
        common = set(vol_rank) & set(beta_rank)
        return {c: (vol_rank[c] + beta_rank[c]) / 2.0 for c in common}

    def _market_returns(self, universe_returns: Dict[str, List[float]], asof_date) -> List[float]:
        """지수 종목 시리즈가 있으면 그 수익률, 없으면 유니버스 일평균 수익률."""
        index_series = self.storage.get_price_series_asof(
            self.market_index_code, days=_DAYS, asof_date=asof_date
        )
        index_ret = self._log_returns(index_series)[-_ONE_YEAR:]
        if len(index_ret) >= 2:
            return index_ret
        return self._universe_avg_returns(universe_returns)

    @staticmethod
    def _universe_avg_returns(universe_returns: Dict[str, List[float]]) -> List[float]:
        """일자별(인덱스 기준) 유니버스 평균 수익률 — 시장 대용 fallback."""
        max_len = max((len(r) for r in universe_returns.values()), default=0)
        if max_len == 0:
            return []
        market = []
        for i in range(max_len):
            day = [r[i] for r in universe_returns.values() if i < len(r)]
            market.append(sum(day) / len(day))
        return market

    @staticmethod
    def _log_returns(prices: List[float]) -> List[float]:
        """일별 로그수익률 ln(P_i/P_{i-1}); 0·음수 가격이 있으면 해당 구간 생략."""
        ret = []
        for i in range(1, len(prices)):
            prev, cur = prices[i - 1], prices[i]
            if prev > 0 and cur > 0:
                ret.append(math.log(cur / prev))
        return ret

    @staticmethod
    def _annualized_vol(log_returns: List[float]) -> Optional[float]:
        """252일 연표준편차 = 일별 로그수익률 표본 std × √252; 수익률 2개 미만이면 None."""
        if len(log_returns) < 2:
            return None
        n = len(log_returns)
        mean = sum(log_returns) / n
        var = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
        return math.sqrt(var) * math.sqrt(_ONE_YEAR)

    @staticmethod
    def _beta(stock_returns: List[float], market_returns: List[float]) -> Optional[float]:
        """cov(개별, 시장)/var(시장) — 수익률 꼬리 정렬, 시장 분산 0이면 None."""
        n = min(len(stock_returns), len(market_returns))
        if n < 2:
            return None
        s = stock_returns[-n:]
        m = market_returns[-n:]
        s_mean = sum(s) / n
        m_mean = sum(m) / n
        cov = sum((a - s_mean) * (b - m_mean) for a, b in zip(s, m)) / n
        var_m = sum((b - m_mean) ** 2 for b in m) / n
        if var_m <= 1e-12:
            return None
        return cov / var_m
