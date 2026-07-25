import logging
import numpy as np
from typing import Dict

logger = logging.getLogger(__name__)


class FactorFeatures:
    def get_all_factors(self, stock_code: str, market_df, pg_conn=None) -> Dict:
        features = {}

        market_cap = self._get_market_cap(stock_code, pg_conn)
        fin_latest, fin_prev = self._get_financials(stock_code, pg_conn)

        features.update(self._value_factors(market_cap, fin_latest, fin_prev))
        features.update(self._quality_factors(market_cap, fin_latest, fin_prev, pg_conn, stock_code))
        features.update(self._momentum_factors(fin_latest, fin_prev, market_df))

        return features

    def _get_market_cap(self, stock_code: str, pg_conn) -> float:
        if pg_conn is None:
            return 0.0
        try:
            cur = pg_conn.cursor()
            cur.execute("SELECT market_cap FROM stocks WHERE stock_code = %s", (stock_code,))
            row = cur.fetchone()
            cur.close()
            return float(row[0]) if row and row[0] else 0.0
        except Exception as e:
            logger.debug(f"market_cap failed for {stock_code}: {e}")
            return 0.0

    def _get_financials(self, stock_code: str, pg_conn):
        latest = {}
        prev = {}
        if pg_conn is None:
            return latest, prev
        try:
            cur = pg_conn.cursor()
            cur.execute("""
                SELECT report_date, revenue, operating_profit, net_income,
                       total_assets, total_equity, per, pbr, roe, debt_ratio
                FROM financial_statements
                WHERE stock_code = %s
                ORDER BY report_date DESC
                LIMIT 4
            """, (stock_code,))
            rows = cur.fetchall()
            cur.close()
            cols = ["report_date", "revenue", "operating_profit", "net_income",
                    "total_assets", "total_equity", "per", "pbr", "roe", "debt_ratio"]
            for i, row in enumerate(rows):
                d = {}
                for j, col in enumerate(cols):
                    val = row[j]
                    d[col] = float(val) if val is not None else 0.0
                if i == 0:
                    latest = d
                elif i == 1:
                    prev = d
        except Exception as e:
            logger.debug(f"financials query failed for {stock_code}: {e}")
            if pg_conn:
                pg_conn.rollback()
        return latest, prev

    def _get_earnings_history(self, stock_code: str, pg_conn):
        values = []
        if pg_conn is None:
            return values
        try:
            cur = pg_conn.cursor()
            cur.execute("""
                SELECT net_income FROM financial_statements
                WHERE stock_code = %s AND net_income IS NOT NULL
                ORDER BY report_date DESC
                LIMIT 8
            """, (stock_code,))
            rows = cur.fetchall()
            cur.close()
            values = [float(r[0]) for r in rows if r[0] is not None]
        except Exception:
            if pg_conn:
                pg_conn.rollback()
        return values

    def _value_factors(self, market_cap: float, latest: Dict, prev: Dict) -> Dict:
        rev = latest.get("revenue", 0.0)
        op = latest.get("operating_profit", 0.0)
        per = latest.get("per", 0.0)
        pbr = latest.get("pbr", 0.0)

        return {
            "value_per": per,
            "value_pbr": pbr,
            "value_psr": (market_cap / rev) if rev > 0 else 0.0,
            "value_pcr": 0.0,
            "value_ncav": 0.0,
            "value_ev_ebit": (market_cap / op) if op > 0 else 0.0,
            "value_pfcr": 0.0,
        }

    def _quality_factors(
        self, market_cap: float, latest: Dict, prev: Dict, pg_conn, stock_code: str
    ) -> Dict:
        op = latest.get("operating_profit", 0.0)
        ni = latest.get("net_income", 0.0)
        assets = latest.get("total_assets", 0.0)
        equity = latest.get("total_equity", 0.0)
        roe = latest.get("roe", 0.0)
        debt_ratio = latest.get("debt_ratio", 0.0)

        prev_assets = prev.get("total_assets", 0.0)
        prev_debt_ratio = prev.get("debt_ratio", 0.0)
        prev_op = prev.get("operating_profit", 0.0)

        asset_growth = ((assets - prev_assets) / prev_assets * 100) if prev_assets > 0 else 0.0
        debt_change = debt_ratio - prev_debt_ratio
        op_growth = ((op - prev_op) / prev_op * 100) if prev_op > 0 else 0.0

        roa = (ni / assets * 100) if assets > 0 else 0.0

        f_score = 0
        if roe > 0:
            f_score += 1
        if latest.get("op_margin", op / latest.get("revenue", 1) * 100 if latest.get("revenue", 0) > 0 else 0) > 0:
            f_score += 1
        if latest.get("net_margin", ni / latest.get("revenue", 1) * 100 if latest.get("revenue", 0) > 0 else 0) > 0:
            f_score += 1
        if debt_ratio < 100:
            f_score += 1
        if latest.get("revenue", 0) > prev.get("revenue", 0) and prev.get("revenue", 0) > 0:
            f_score += 1

        earnings_vol = 0.0
        earnings_hist = self._get_earnings_history(stock_code, pg_conn)
        if len(earnings_hist) >= 3:
            earnings_vol = float(np.std(earnings_hist))

        return {
            "quality_cp_to_assets": 0.0,
            "quality_op_to_equity": (op / equity) if equity > 0 else 0.0,
            "quality_roe": roe,
            "quality_roa": roa,
            "quality_f_score": float(f_score),
            "quality_asset_growth": asset_growth,
            "quality_debt_ratio_change": debt_change,
            "quality_op_growth": op_growth,
            "quality_earnings_volatility": earnings_vol,
            "quality_price_volatility_60d": self._price_volatility_60d(market_df=None),
            "quality_beta": self._beta(market_df=None),
        }

    def _price_volatility_60d(self, market_df) -> float:
        close = self._get_close(market_df)
        if close is None or len(close) < 21:
            return 0.0
        rets = [(close[i] / close[i - 1] - 1) for i in range(1, len(close))]
        if len(rets) < 20:
            return 0.0
        return float(np.std(rets[-60:]) * np.sqrt(252)) if len(rets) >= 60 else float(np.std(rets) * np.sqrt(252))

    def _beta(self, market_df) -> float:
        close = self._get_close(market_df)
        if close is None or len(close) < 61:
            return 0.0
        rets = [(close[i] / close[i - 1] - 1) for i in range(1, len(close))]
        stock_rets = rets[-60:]
        mkt_rets = stock_rets
        if len(stock_rets) < 2:
            return 0.0
        cov = float(np.cov(stock_rets, mkt_rets)[0][1]) if len(stock_rets) >= 2 else 0.0
        var_mkt = float(np.var(mkt_rets)) if len(mkt_rets) >= 2 else 0.0
        return (cov / var_mkt) if var_mkt > 0 else 1.0

    def _get_close(self, market_df):
        if market_df is None:
            return None
        if hasattr(market_df, "get"):
            close = market_df.get("close_price", market_df.get("close"))
            if close is not None and len(close) > 0:
                vals = close.values if hasattr(close, "values") else np.array(close)
                return np.array([float(c) for c in vals])
        return None

    def _momentum_factors(self, latest: Dict, prev: Dict, market_df) -> Dict:
        op = latest.get("operating_profit", 0.0)
        ni = latest.get("net_income", 0.0)
        prev_op = prev.get("operating_profit", 0.0)
        prev_ni = prev.get("net_income", 0.0)

        op_change = ((op - prev_op) / prev_op * 100) if prev_op > 0 else 0.0
        ni_change = ((ni - prev_ni) / prev_ni * 100) if prev_ni > 0 else 0.0

        close = self._get_close(market_df)
        ret_1m = 0.0
        ret_3m = 0.0
        ret_12m = 0.0
        if close is not None:
            valid = len(close)
            ret_1m = float(close[-1] / close[-21] - 1) if valid >= 21 else 0.0
            ret_3m = float(close[-1] / close[-63] - 1) if valid >= 63 else 0.0
            ret_12m = float(close[-1] / close[-252] - 1) if valid >= 252 else ret_3m

        return {
            "momentum_1m_reverse": -1.0 * ret_1m,
            "momentum_3_12m": ret_12m - ret_3m,
            "momentum_op": op_change,
            "momentum_ni": ni_change,
        }
