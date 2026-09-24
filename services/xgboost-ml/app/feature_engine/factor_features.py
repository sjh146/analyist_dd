import logging
import numpy as np
from typing import Dict, Optional, Tuple

from app.feature_engine.market_data_filter import MARKET_DATA_VALID

logger = logging.getLogger(__name__)

# financial_statements 계약(실측 2026-09-24): report_date 는 **기간 말일**이고
# 2026-06-30 행은 반기(6개월 누적), 2025-12-31/2024-12-31 행은 연간(12개월)이다
# (삼성전자 revenue 2026-06-30 171.5조 = FY2025 333.6조의 약 절반).
# → 현금흐름(flow) 계열 비율은 **연간(12월 결산) 행**, 재무상태표(stock) 계열 비율은
#   **최신 행**을 써서 기간을 섞지 않는다. (아래 _get_annual_financials 참고)
FIN_COLUMNS = [
    "report_date", "revenue", "operating_profit", "net_income",
    "total_assets", "total_equity", "per", "pbr", "roe", "debt_ratio",
    "operating_cash_flow", "total_debt", "gross_profit",
]

# 시장(동일가중) 일별 수익률 캐시: 프로세스당 1회만 계산한다.
# (패널 빌드는 종목×날짜 단위로 수천 번 호출되므로 쿼리를 매번 돌리면 안 된다.)
_MARKET_RET_CACHE: Dict[str, dict] = {"series": None}
# financial_statements 컬럼 프로브 캐시 (NCAV 등 조건부 피처용)
_FIN_COL_CACHE: Dict[str, Optional[set]] = {"cols": None}


class FactorFeatures:
    def get_all_factors(self, stock_code: str, market_df, pg_conn=None) -> Dict:
        features = {}

        market_cap = self._get_market_cap(stock_code, pg_conn)
        fin_latest, fin_prev = self._get_financials(stock_code, pg_conn)
        annual, annual_prev = self._get_annual_financials(stock_code, pg_conn)
        # 유동자산/유동부채 컬럼이 아직 없으면 None → value_ncav 는 0.0 유지.
        ca, cl = self._get_current_items(stock_code, pg_conn)

        features.update(self._value_factors(market_cap, fin_latest, fin_prev, annual, annual_prev,
                                            ca, cl))
        features.update(self._quality_factors(market_cap, fin_latest, fin_prev, pg_conn, stock_code,
                                              market_df, annual, annual_prev))
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
                       total_assets, total_equity, per, pbr, roe, debt_ratio,
                       operating_cash_flow, total_debt, gross_profit
                FROM financial_statements
                WHERE stock_code = %s
                ORDER BY report_date DESC
                LIMIT 4
            """, (stock_code,))
            rows = cur.fetchall()
            cur.close()
            cols = FIN_COLUMNS
            for i, row in enumerate(rows):
                d = {}
                for j, col in enumerate(cols):
                    val = row[j]
                    # 실측 버그 수정(2026-09-24): report_date 는 date 형이라 float() 가
                    # TypeError 를 던져, 이 종목의 밸류/퀄리티/모멘텀 피처가 **전부** 기본값
                    # 0.0 으로 떨어졌다(DB 에 데이터가 있어도 상수 0). 날짜 컬럼은 원값 유지.
                    if col == "report_date":
                        d[col] = val
                    else:
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

    def _get_annual_financials(self, stock_code: str, pg_conn):
        """가장 최근 **연간(12월 결산)** 행과 그 직전 연간 행.

        WHY: 반기(2026-06-30) 행과 연간(2025-12-31) 행을 그대로 비교하면 현금흐름
        비율이 최대 2배 왜곡된다(실측 삼성전자 revenue 171.5조 vs 333.6조).
        현금흐름 기반 비율(PCR/PFCR/CP-to-assets)은 연간 행으로만 계산한다.
        """
        annual = {}
        annual_prev = {}
        if pg_conn is None:
            return annual, annual_prev
        try:
            cur = pg_conn.cursor()
            cur.execute("""
                SELECT report_date, revenue, operating_profit, net_income,
                       total_assets, total_equity, per, pbr, roe, debt_ratio,
                       operating_cash_flow, total_debt, gross_profit
                FROM financial_statements
                WHERE stock_code = %s AND EXTRACT(MONTH FROM report_date) = 12
                ORDER BY report_date DESC
                LIMIT 2
            """, (stock_code,))
            rows = cur.fetchall()
            cur.close()
            for i, row in enumerate(rows):
                d = {}
                for j, col in enumerate(FIN_COLUMNS):
                    val = row[j]
                    d[col] = val if col == "report_date" else (float(val) if val is not None else 0.0)
                if i == 0:
                    annual = d
                elif i == 1:
                    annual_prev = d
        except Exception as e:
            logger.debug(f"annual financials query failed for {stock_code}: {e}")
            if pg_conn:
                pg_conn.rollback()
        return annual, annual_prev

    def _get_current_items(self, stock_code: str, pg_conn):
        """(current_assets, current_liabilities) — 컬럼이 없으면 (None, None).

        NCAV(= 유동자산 − 총부채) 계산에 필요한 컬럼. 현재 financial_statements 에는
        없으므로 컬럼 존재를 1회 프로브하고, 없으면 조용히 None 을 돌려준다
        (다른 작업자가 컬럼을 추가하면 별도 수정 없이 value_ncav 가 살아난다).
        """
        if pg_conn is None:
            return None, None
        global _FIN_COL_CACHE
        try:
            if _FIN_COL_CACHE["cols"] is None:
                cur = pg_conn.cursor()
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'financial_statements'
                """)
                _FIN_COL_CACHE["cols"] = {r[0] for r in cur.fetchall()}
                cur.close()
            cols = _FIN_COL_CACHE["cols"]
            have_ca = "current_assets" in cols
            have_cl = "current_liabilities" in cols
            if not (have_ca or have_cl):
                return None, None
            picks = ", ".join(
                c for c in ("current_assets", "current_liabilities") if c in cols
            )
            cur = pg_conn.cursor()
            cur.execute(f"""
                SELECT {picks} FROM financial_statements
                WHERE stock_code = %s ORDER BY report_date DESC LIMIT 1
            """, (stock_code,))
            row = cur.fetchone()
            cur.close()
            if not row:
                return None, None
            vals = [float(v) if v is not None else None for v in row]
            ca = vals[0] if have_ca else None
            cl = vals[1] if (have_ca and have_cl) else (vals[0] if have_cl else None)
            return ca, cl
        except Exception as e:
            logger.debug(f"current items probe failed for {stock_code}: {e}")
            if pg_conn:
                pg_conn.rollback()
            return None, None

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

    def _value_factors(
        self, market_cap: float, latest: Dict, prev: Dict,
        annual: Optional[Dict] = None, annual_prev: Optional[Dict] = None,
        current_assets: Optional[float] = None, current_liabilities: Optional[float] = None,
    ) -> Dict:
        annual = annual or {}
        annual_prev = annual_prev or {}
        rev = latest.get("revenue", 0.0)
        op = latest.get("operating_profit", 0.0)
        per = latest.get("per", 0.0)
        pbr = latest.get("pbr", 0.0)

        # --- 현금흐름 계열: 반드시 연간(12월) 행 사용 (기간 페어링) ---
        ocf_fy = annual.get("operating_cash_flow", 0.0)
        assets_fy = annual.get("total_assets", 0.0)
        assets_fy_prev = annual_prev.get("total_assets", 0.0)
        # CAPEX(유형자산 취득) 컬럼이 DB에 없다 → 순자산 증가분을 재투자 프록시로 쓴다.
        # (필요 컬럼: financial_statements.capex 또는 investing_cash_flow — 보고서 참고)
        reinvest_proxy = max(0.0, assets_fy - assets_fy_prev) if assets_fy_prev > 0 else 0.0
        fcf_fy = ocf_fy - reinvest_proxy

        # --- 재무상태표 계열: 최신 행(시점 값이므로 기간 무관) ---
        assets = latest.get("total_assets", 0.0)
        equity = latest.get("total_equity", 0.0)
        # NCAV = 유동자산 − 총부채. financial_statements 에는 유동자산/유동부채 컬럼이 없다.
        # 실측(2026-09-24): total_debt 는 DART "부채총계"(= total_assets − total_equity)라
        #   자산총계 − total_debt ≡ 자기자본 → value_ncav 가 value_pbr 과 완전 중복이 된다.
        #   → 유동자산 컬럼이 생기기 전까지는 0.0 을 유지한다(중복 피처 주입 금지).
        ncav_proxy = 0.0
        if current_assets is not None:
            liab = current_liabilities if current_liabilities is not None else (assets - equity)
            ncav_proxy = current_assets - liab

        return {
            "value_per": per,
            "value_pbr": pbr,
            "value_psr": (market_cap / rev) if rev > 0 else 0.0,
            "value_pcr": (market_cap / ocf_fy) if (market_cap > 0 and ocf_fy > 0) else 0.0,
            "value_ncav": (market_cap / ncav_proxy) if (market_cap > 0 and ncav_proxy > 0) else 0.0,
            "value_ev_ebit": (market_cap / op) if op > 0 else 0.0,
            "value_pfcr": (market_cap / fcf_fy) if (market_cap > 0 and fcf_fy > 0) else 0.0,
        }

    def _quality_factors(
        self, market_cap: float, latest: Dict, prev: Dict, pg_conn, stock_code: str,
        market_df=None, annual: Optional[Dict] = None, annual_prev: Optional[Dict] = None,
    ) -> Dict:
        annual = annual or {}
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
            # CP = "cash provided"(영업활동현금흐름) → 자산 대비 현금창출 비율.
            # 연간 OCF / 최신 자산 (flow는 연간, stock은 최신 — 기간 페어링 준수)
            "quality_cp_to_assets": (
                annual.get("operating_cash_flow", 0.0) / assets if assets > 0 else 0.0
            ),
            "quality_op_to_equity": (op / equity) if equity > 0 else 0.0,
            "quality_roe": roe,
            "quality_roa": roa,
            "quality_f_score": float(f_score),
            "quality_asset_growth": asset_growth,
            "quality_debt_ratio_change": debt_change,
            "quality_op_growth": op_growth,
            "quality_earnings_volatility": earnings_vol,
            # 실측 버그 수정(2026-09-24): market_df=None 을 하드코딩해 전 종목 0.0 상수였다.
            # (호출자 get_all_factors 는 market_df 를 이미 갖고 있다)
            "quality_price_volatility_60d": self._price_volatility_60d(market_df),
            # quality_beta: 2026-09-24 수정. 기존 _beta() 는 시장수익률 자리에 자기 자신을
            # 넣어 cov(x,x)/var(x) ≡ 1.0 상수를 반환했다(β 아님) → market_data 전체 종목의
            # 동일가중 일별 수익률을 시장수익률로 삼아 진짜 β 를 계산한다.
            "quality_beta": self._beta(market_df, pg_conn),
        }

    def _price_volatility_60d(self, market_df) -> float:
        close = self._get_close(market_df)
        if close is None or len(close) < 21:
            return 0.0
        rets = [(close[i] / close[i - 1] - 1) for i in range(1, len(close))]
        if len(rets) < 20:
            return 0.0
        return float(np.std(rets[-60:]) * np.sqrt(252)) if len(rets) >= 60 else float(np.std(rets) * np.sqrt(252))

    def _market_returns(self, pg_conn) -> Dict[str, float]:
        """시장수익률(동일가중) 일별 시계열 {YYYY-MM-DD: 수익률} — 프로세스당 1회 계산.

        지수(KOSPI) 시계열이 DB에 없으므로, market_data 전체 종목(STOCK)의
        동일가중 평균 일별 수익률을 시장수익률로 정의한다(횡단면 평균 → 시장 요인).
        """
        if _MARKET_RET_CACHE.get("series") is not None:
            return _MARKET_RET_CACHE["series"]
        series: Dict[str, float] = {}
        if pg_conn is None:
            return series
        try:
            cur = pg_conn.cursor()
            cur.execute(f"""
                WITH px AS (
                    SELECT md.stock_code, md.trade_date, md.close_price
                    FROM market_data md
                    JOIN stocks s ON s.stock_code = md.stock_code
                    WHERE s.instrument_type = 'STOCK'
                      AND md.trade_date >= (SELECT max(trade_date) FROM market_data) - INTERVAL '400 days'
                      AND {MARKET_DATA_VALID}
                ), r AS (
                    SELECT trade_date,
                           close_price / NULLIF(LAG(close_price) OVER (
                               PARTITION BY stock_code ORDER BY trade_date), 0) - 1 AS ret
                    FROM px
                )
                SELECT trade_date::text, AVG(ret)
                FROM r
                WHERE ret IS NOT NULL AND abs(ret) < 0.5
                GROUP BY trade_date
            """)
            for d, v in cur.fetchall():
                if v is not None:
                    series[str(d)] = float(v)
            cur.close()
            logger.info(f"market return series loaded: {len(series)} dates")
        except Exception as e:
            logger.debug(f"market return series failed: {e}")
            if pg_conn:
                pg_conn.rollback()
        _MARKET_RET_CACHE["series"] = series
        return series

    def _get_dates(self, market_df):
        if market_df is None or "trade_date" not in getattr(market_df, "columns", []):
            return None
        try:
            return [str(d)[:10] for d in market_df["trade_date"]]
        except Exception:
            return None

    def _beta(self, market_df, pg_conn=None) -> float:
        """60일 베타 = cov(종목수익률, 시장수익률) / var(시장수익률).

        기존 구현은 market_df=None 을 넘겨 항상 0.0 이었고, mkt_rets=stock_rets 로
        자기 자신과의 공분산이라 1.0 상수였다 → 두 번 모두 무효. 지금은 market_data
        횡단면 동일가중 수익률을 시장수익률로 써서 종목별로 다른 값을 낸다.
        """
        close = self._get_close(market_df)
        if close is None or len(close) < 21:
            return 0.0
        dates = self._get_dates(market_df)
        mkt = self._market_returns(pg_conn)
        if not mkt:
            return 0.0
        if dates is not None and len(dates) == len(close):
            pairs = []
            for i in range(1, len(close)):
                d = dates[i]
                m = mkt.get(d)
                if m is None or close[i - 1] == 0:
                    continue
                pairs.append((close[i] / close[i - 1] - 1, m))
            window = pairs[-60:]
        else:
            # 날짜 정렬이 불가하면 시장 시계열의 최근 60일과 위치로 맞춘다.
            mkt_vals = [mkt[k] for k in sorted(mkt)][-61:]
            rets = [(close[i] / close[i - 1] - 1) for i in range(1, len(close))]
            if len(mkt_vals) >= 2:
                rets = rets[-len(mkt_vals):]
                pairs = list(zip(rets, mkt_vals[1:]))
            else:
                return 0.0
            window = pairs
        if len(window) < 10:
            return 0.0
        s = np.array([p[0] for p in window], dtype=np.float64)
        m = np.array([p[1] for p in window], dtype=np.float64)
        var_mkt = float(np.var(m))
        if var_mkt <= 0:
            return 0.0
        return float(np.cov(s, m)[0][1] / var_mkt)

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
