import logging
import statistics
from typing import List, Optional, Dict, Callable

from app.data_quality.validator import Validator
from app.data_quality.range_rule import RangeRule
from app.data_quality.zscore_rule import ZScoreRule
from app.data_quality.rate_change_rule import RateChangeRule
from app.data_quality.null_ratio_rule import NullRatioRule

logger = logging.getLogger(__name__)


class DataQualityIntegration:
    def __init__(self, db_conn_provider: Optional[Callable] = None):
        self.validator = Validator()
        self.zscore_rule = ZScoreRule(threshold=3.0)
        self.rate_change_rule = RateChangeRule(max_change_pct=50.0)
        self.null_ratio_rule = NullRatioRule(max_null_ratio=0.05)

        self.validator.add_rule(RangeRule(-1.0, 1.0, name="sentiment_score_range"))
        self.validator.add_rule(self.zscore_rule)
        self.validator.add_rule(self.rate_change_rule)
        self.validator.add_rule(self.null_ratio_rule)

        self._db_provider = db_conn_provider

    def _fetch_recent_scores(
        self, stock_code: str, limit: int = 100
    ) -> List[float]:
        if not self._db_provider:
            return []
        conn = self._db_provider()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT avg_sentiment FROM stock_sentiment
                WHERE stock_code = %s AND avg_sentiment IS NOT NULL
                ORDER BY analysis_date DESC LIMIT %s
            """,
                (stock_code, limit),
            )
            rows = cur.fetchall()
            cur.close()
            return [float(r[0]) for r in rows if r[0] is not None]
        except Exception as e:
            logger.debug(
                f"Failed to fetch recent scores for {stock_code}: {e}"
            )
            return []

    def validate_sentiment(
        self,
        sentiment_score: float,
        stock_code: str,
        batch_scores: Optional[List[float]] = None,
    ) -> Dict:
        recent = self._fetch_recent_scores(stock_code)
        if recent:
            self.zscore_rule.set_stats(
                mean=statistics.mean(recent),
                std=statistics.stdev(recent) if len(recent) > 1 else 1.0,
            )
            self.rate_change_rule.set_previous(recent[0] if recent else None)

        if batch_scores:
            result = self.validator.validate_batch(batch_scores)
        else:
            result = self.validator.validate_value(sentiment_score)

        return result

    @staticmethod
    def get_overall_result(validation_result: Dict) -> str:
        if validation_result.get("failed", 0) > 0:
            return "fail"
        if validation_result.get("warned", 0) > 0:
            return "warn"
        return "pass"

    def log_validation_result(
        self,
        sentiment_score: float,
        stock_code: str,
        article_title: str,
        validation_result: Dict,
    ):
        overall = self.get_overall_result(validation_result)
        details = validation_result.get("details", [])
        detail_str = "; ".join(
            f"{d['rule']}={d['result']}" for d in details
        )

        if overall == "pass":
            logger.info(
                f"Validation PASS | stock={stock_code} "
                f"score={sentiment_score:.4f} | {detail_str}"
            )
        elif overall == "warn":
            logger.warning(
                f"Validation WARN | stock={stock_code} "
                f"score={sentiment_score:.4f} | {detail_str} | "
                f"article='{article_title[:50]}'"
            )
        else:
            logger.error(
                f"Validation FAIL | stock={stock_code} "
                f"score={sentiment_score:.4f} | {detail_str} | "
                f"article='{article_title[:50]}'"
            )
