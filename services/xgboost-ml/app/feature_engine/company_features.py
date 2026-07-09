"""
Company Features
Extracts fundamental features from financial statement data (DART).
"""

import logging
import numpy as np
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class CompanyFeatures:
    """Features derived from financial statements: PER, PBR, ROE, growth rates."""

    def get_financial_features(
        self, stock_code: str, db_conn=None
    ) -> Dict:
        """Build 10+ fundamental features from financial_statements table."""
        features = {
            "revenue": 0.0, "operating_profit": 0.0, "net_income": 0.0,
            "op_margin": 0.0, "net_margin": 0.0,
            "per_current": 0.0, "pbr_current": 0.0,
            "roe": 0.0, "debt_ratio": 0.0,
            "revenue_growth_yoy": 0.0, "op_margin_change_yoy": 0.0,
        }

        if db_conn is None:
            return features

        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT report_date, revenue, operating_profit, net_income,
                       per, pbr, roe, debt_ratio, total_assets, total_equity
                FROM financial_statements
                WHERE stock_code = %s
                ORDER BY report_date DESC
                LIMIT 2
            """, (stock_code,))
            rows = cur.fetchall()
            cur.close()

            if not rows:
                return features

            latest = rows[0]
            features["revenue"] = float(latest[1]) if latest[1] else 0.0
            features["operating_profit"] = float(latest[2]) if latest[2] else 0.0
            features["net_income"] = float(latest[3]) if latest[3] else 0.0
            features["per_current"] = float(latest[4]) if latest[4] else 0.0
            features["pbr_current"] = float(latest[5]) if latest[5] else 0.0
            features["roe"] = float(latest[6]) if latest[6] else 0.0
            features["debt_ratio"] = float(latest[7]) if latest[7] else 0.0

            rev = features["revenue"]
            op = features["operating_profit"]
            ni = features["net_income"]

            features["op_margin"] = (op / rev * 100) if rev else 0.0
            features["net_margin"] = (ni / rev * 100) if rev else 0.0

            if len(rows) >= 2:
                prev = rows[1]
                prev_rev = float(prev[1]) if prev[1] else 0.0
                prev_op = float(prev[2]) if prev[2] else 0.0

                features["revenue_growth_yoy"] = (
                    (rev - prev_rev) / prev_rev * 100
                ) if prev_rev else 0.0

                prev_op_margin = (prev_op / prev_rev * 100) if prev_rev else 0.0
                features["op_margin_change_yoy"] = features["op_margin"] - prev_op_margin

        except Exception as e:
            logger.debug(f"Financial features failed for {stock_code}: {e}")

        return features

    def get_percentile_features(self, stock_code: str, db_conn=None) -> Dict:
        """Calculate PER/PBR percentile within sector."""
        features = {"per_percentile": 50.0, "pbr_percentile": 50.0}

        if db_conn is None:
            return features

        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT sector FROM stocks WHERE stock_code = %s
            """, (stock_code,))
            sector_row = cur.fetchone()
            if not sector_row or not sector_row[0]:
                cur.close()
                return features
            sector = sector_row[0]
            cur.close()

            cur = db_conn.cursor()
            cur.execute("""
                SELECT per, pbr
                FROM financial_statements fs
                JOIN stocks s ON fs.stock_code = s.stock_code
                WHERE s.sector = %s
                  AND fs.per > 0 AND fs.pbr > 0
                  AND fs.report_date = (
                      SELECT MAX(report_date) FROM financial_statements
                      WHERE stock_code = fs.stock_code
                  )
            """, (sector,))
            all_rows = cur.fetchall()
            cur.close()

            if all_rows:
                per_vals = sorted([r[0] for r in all_rows if r[0]])
                pbr_vals = sorted([r[1] for r in all_rows if r[1]])

                my_per = features.get("per_current", 0)
                my_pbr = features.get("pbr_current", 0)

                if per_vals and my_per > 0:
                    rank = sum(1 for p in per_vals if p <= my_per)
                    features["per_percentile"] = (rank / len(per_vals)) * 100

                if pbr_vals and my_pbr > 0:
                    rank = sum(1 for p in pbr_vals if p <= my_pbr)
                    features["pbr_percentile"] = (rank / len(pbr_vals)) * 100

        except Exception as e:
            logger.debug(f"Percentile features failed for {stock_code}: {e}")

        return features

    def get_all_features(self, stock_code: str, db_conn=None) -> Dict:
        """Get all company fundamental features."""
        features = {}
        features.update(self.get_financial_features(stock_code, db_conn))
        features.update(self.get_percentile_features(stock_code, db_conn))
        return features
