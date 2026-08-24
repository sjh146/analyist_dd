"""
SNS Lag Features (Phase D of the SNS intelligence pipeline)
===========================================================

Computes per-stock price-vs-SNS **lead/lag (cross-correlation)** features.
For each of the 4 SNS features (sentiment / attention / momentum /
author_quality), it cross-correlates the stock's daily return series with a
shifted version of the SNS feature series across lags -5..+5 days, and reports
the lag with the maximum |correlation| plus that correlation's magnitude.

Design
------
The core ``compute_for_stock`` works on **in-memory** DataFrames so unit tests
run with NO database. A DB-backed ``get_all_features`` convenience defaults to
0.0 / lag 0 when ``db_conn`` is None or the query fails (fail-open — mirrors
``news_event_features.py``).

Sign convention
---------------
``best_lag = argmax_lag |corr(f[t - lag], r[t])|``
- ``best_lag > 0`` : the SNS feature is best correlated with the return lag days
  *earlier* — i.e. SNS leads price (price lags SNS).
- ``best_lag < 0`` : the SNS feature is best correlated with the return
  |lag| days *later* — i.e. price leads SNS.
``lag_sign = +1`` when price leads (best_lag < 0), ``-1`` when SNS leads
(best_lag > 0), ``0`` when best_lag == 0 or no data.

Output keys (flat, snake_case)
------------------------------
- ``sns_{feature}_best_lag``   : int
- ``sns_{feature}_max_corr``   : float in [0, 1]
- ``sns_{feature}_lag_sign``   : int in {-1, 0, +1}
- ``sns_{feature}_corr0``      : float in [-1, 1] (correlation at lag 0)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_EPS = 1e-8


class SnsLagFeatures:
    """Price-vs-SNS lead/lag cross-correlation features."""

    # SNS 4대 피처 — sns_features.py 의 산식과 일치하는 컬럼명.
    FEATURES = [
        "sentiment_score",
        "attention_score",
        "momentum_score",
        "author_quality_score",
    ]
    LAG_RANGE = 5          # -5..+5 일 교차상관.
    MIN_PAIRS = 8          # 유효 관측 짝 최소 수 (미달 시 corr 0.0 / lag 0).

    # ── 파생 피처 키 템플릿 ─────────────────────────────────────────────
    @property
    def feature_keys(self) -> List[str]:
        keys = []
        for f in self.FEATURES:
            keys += [
                f"sns_{f}_best_lag",
                f"sns_{f}_max_corr",
                f"sns_{f}_lag_sign",
                f"sns_{f}_corr0",
            ]
        return keys

    # ── 순수 계산 코어 ──────────────────────────────────────────────────
    def _align_series(self, dates_sns, values_sns, dates_ret, values_ret):
        """SNS 피처와 수익률을 공통 일자 기준으로 정렬한 (values_sns, values_ret)."""
        idx = {d: v for d, v in zip(dates_sns, values_sns)}
        ret = {d: v for d, v in zip(dates_ret, values_ret)}
        common = sorted(set(idx.keys()) & set(ret.keys()))
        if len(common) < 2:
            return np.array([], dtype=float), np.array([], dtype=float)
        return (
            np.asarray([idx[d] for d in common], dtype=float),
            np.asarray([ret[d] for d in common], dtype=float),
        )

    def _cross_correlation(self, feature_values: np.ndarray, returns: np.ndarray,
                           lag: int) -> Optional[float]:
        """시프트 라그에 대한 상관 corr(f[t-lag], r[t]).

        lag > 0: SNS 값이 그만큼 '앞서' 가격 수익률과 상관되므로 SNS 리드.
        lag < 0: 수익률이 SNS보다 앞서므로 가격 리드.
        유효 관측 짝이 MIN_PAIRS 미만이거나 분산 0이면 None.
        """
        n = len(returns)
        # f[t-lag]: lag>0 이면 SNS를 왼쪽으로 lag 칸 이동 → 시프트 배열.
        a = feature_values
        b = returns
        if lag > 0:
            fa, fb = a[: n - lag], b[lag:]
        elif lag < 0:
            fa, fb = a[-lag:], b[: n + lag]
        else:
            fa, fb = a, b
        if len(fa) < self.MIN_PAIRS or len(fb) < self.MIN_PAIRS:
            return None
        fa = fa[np.isfinite(fa)]
        fb = fb[np.isfinite(fb)]
        if len(fa) < self.MIN_PAIRS or len(fb) < self.MIN_PAIRS or len(fa) != len(fb):
            return None
        if np.std(fa) < _EPS or np.std(fb) < _EPS:
            return None
        return float(np.corrcoef(fa, fb)[0, 1])

    def compute_for_stock(self, sns_df: pd.DataFrame, price_df: pd.DataFrame,
                          stock_code: Optional[str] = None) -> Dict:
        """종목별 가격–SNS 시차 피처 dict를 반환한다.

        Parameters
        ----------
        sns_df : DataFrame
            일별 SNS 피처. ``trade_date``(주간 정렬) + ``<FEATURES>`` 컬럼.
        price_df : DataFrame
            ``trade_date`` 와 ``close`` (또는 ``return``) 컬럼.
        stock_code : str, optional
            메타로 결과에 포함 (없으면 생략).

        Returns
        -------
        dict
            ``sns_<feature>_*`` 피처 키. 데이터 부족 시 0.0 / best_lag 0.
        """
        # 수익률 계열 구하기.
        if "return" in price_df.columns:
            p_ret = price_df[["trade_date", "return"]].drop_duplicates("trade_date")
        else:
            p = price_df[["trade_date", "close"]].drop_duplicates("trade_date").sort_values("trade_date")
            p = p[p["close"].notna() & (p["close"] > 0)]
            prices = p["close"].to_numpy(dtype=float)
            ret = np.zeros_like(prices)
            if len(prices) > 1:
                ret[1:] = np.diff(prices) / prices[:-1]
            p_ret = pd.DataFrame({"trade_date": p["trade_date"].to_numpy(), "return": ret})

        dates_ret = p_ret["trade_date"].tolist()
        values_ret = p_ret["return"].to_numpy(dtype=float)

        # SNS 피처 컬럼 형식 (datetime.date 또는 pd.Timestamp) 통일.
        sns = sns_df.copy()
        sns["trade_date"] = pd.to_datetime(sns["trade_date"]).dt.date

        result: Dict = {}
        for f in self.FEATURES:
            if f not in sns.columns:
                result.update(
                    {
                        f"sns_{f}_best_lag": 0,
                        f"sns_{f}_max_corr": 0.0,
                        f"sns_{f}_lag_sign": 0,
                        f"sns_{f}_corr0": 0.0,
                    }
                )
                continue
            sub = sns[["trade_date", f]].dropna()
            sub = sub.drop_duplicates("trade_date")
            dates_s = sub["trade_date"].tolist()
            vals_s = sub[f].to_numpy(dtype=float)
            feat, ret = self._align_series(dates_s, vals_s, dates_ret, values_ret)
            if len(feat) < 2:
                result.update(
                    {
                        f"sns_{f}_best_lag": 0,
                        f"sns_{f}_max_corr": 0.0,
                        f"sns_{f}_lag_sign": 0,
                        f"sns_{f}_corr0": 0.0,
                    }
                )
                continue
            best_lag, best_corr, corr0 = 0, 0.0, 0.0
            for lag in range(-self.LAG_RANGE, self.LAG_RANGE + 1):
                c = self._cross_correlation(feat, ret, lag)
                if c is None:
                    continue
                if lag == 0:
                    corr0 = c
                if abs(c) > abs(best_corr):
                    best_corr, best_lag = c, lag
            lag_sign = 0
            if best_lag > 0:
                lag_sign = -1  # SNS 리드 (가격 래그).
            elif best_lag < 0:
                lag_sign = +1  # 가격 리드.
            result.update(
                {
                    f"sns_{f}_best_lag": int(best_lag),
                    f"sns_{f}_max_corr": float(min(abs(best_corr), 1.0)),
                    f"sns_{f}_lag_sign": int(lag_sign),
                    f"sns_{f}_corr0": float(corr0),
                }
            )
        if stock_code is not None:
            result["stock_code"] = stock_code
        return result

    def compute_for_many(self, sns_panel: pd.DataFrame, price_panel: pd.DataFrame) -> List[Dict]:
        """종목 패널 DataFrame 들을 순회해 종목별 피처 dict 목록을 반환한다.

        두 입력 모두 ``stock_code`` 를 가져야 한다.
        """
        results: List[Dict] = []
        for code in sorted(sns_panel["stock_code"].dropna().unique()):
            s = sns_panel[sns_panel["stock_code"] == code]
            p = price_panel[price_panel["stock_code"] == code]
            results.append(self.compute_for_stock(s, p, stock_code=code))
        return results

    # ── DB 편의 메서드 (fail-open) ─────────────────────────────────────
    def get_all_features(self, stock_code: str, db_conn=None) -> Dict:
        """DB 기반 종목 피처 조회 편의. db_conn None/실패 시 기본 0.0 dict."""
        if db_conn is None:
            result = dict.fromkeys(self.feature_keys, 0.0)
            for f in self.FEATURES:
                result[f"sns_{f}_best_lag"] = 0
                result[f"sns_{f}_lag_sign"] = 0
            return result
        try:
            cur = db_conn.cursor()
            cur.execute(
                """
                SELECT trade_date, sentiment_score, attention_score,
                       momentum_score, author_quality_score
                FROM sns_post_features
                WHERE stock_code = %s
                ORDER BY trade_date
                """,
                (stock_code,),
            )
            sns_rows = cur.fetchall()
            cur.execute(
                """
                SELECT trade_date, close_price
                FROM market_data
                WHERE stock_code = %s
                ORDER BY trade_date
                """,
                (stock_code,),
            )
            price_rows = cur.fetchall()
            cur.close()
            if not sns_rows or not price_rows:
                raise ValueError("insufficient rows")
            sns_df = pd.DataFrame(
                sns_rows,
                columns=["trade_date", *self.FEATURES],
            )
            price_df = pd.DataFrame(price_rows, columns=["trade_date", "close"])
            return self.compute_for_stock(sns_df, price_df, stock_code=stock_code)
        except Exception as e:
            logger.debug("sns lag features failed for %s: %s", stock_code, e)
            if db_conn:
                try:
                    db_conn.rollback()
                except Exception:
                    pass
            result = dict.fromkeys(self.feature_keys, 0.0)
            for f in self.FEATURES:
                result[f"sns_{f}_best_lag"] = 0
                result[f"sns_{f}_lag_sign"] = 0
            if stock_code is not None:
                result["stock_code"] = stock_code
            return result
