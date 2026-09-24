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

    def get_percentile_features(
        self, stock_code: str, db_conn=None, date: Optional[str] = None
    ) -> Dict:
        """Calculate PER/PBR percentile against peers.

        실측 수정(2026-09-24): 종전 구현은 두 가지 이유로 **항상 50.0 상수**였다
        (feature_coverage 실측: per_percentile/pbr_percentile nonzero_ratio=1, std=0).
          1) `my_per = features.get("per_current", 0)` — features 는 이 메서드 안에서
             {"per_percentile": 50.0, "pbr_percentile": 50.0} 로 새로 만든 dict 라
             'per_current' 키가 없다 → my_per = 0 → `my_per > 0` 이 항상 거짓.
          2) 섹터: stocks.sector 는 시세 보유 종목 중 ETF 16건만 채워져 있어
             `sector IS NULL` 이면 즉시 50.0 을 반환했다.
        수정: 자기 per/pbr 를 DB 에서 직접 읽고, 섹터가 없으면 전종목(시장 전체)을
        피어 집합으로 쓴다(가치주 상대 위치라는 피처 의미는 유지된다).
        date 가 주어지면 report_date <= date 인 최신 재무제표만 사용한다(미래참조 금지).
        """
        features = {"per_percentile": 50.0, "pbr_percentile": 50.0}

        if db_conn is None:
            return features

        try:
            cur = db_conn.cursor()

            # 1) 자기 섹터 + 자기 최신 per/pbr
            if date:
                cur.execute("""
                    SELECT s.sector,
                           (SELECT f.per FROM financial_statements f
                             WHERE f.stock_code = s.stock_code AND f.report_date <= %s
                             ORDER BY f.report_date DESC LIMIT 1),
                           (SELECT f.pbr FROM financial_statements f
                             WHERE f.stock_code = s.stock_code AND f.report_date <= %s
                             ORDER BY f.report_date DESC LIMIT 1)
                    FROM stocks s WHERE s.stock_code = %s
                """, (date, date, stock_code))
            else:
                cur.execute("""
                    SELECT s.sector,
                           (SELECT f.per FROM financial_statements f
                             WHERE f.stock_code = s.stock_code
                             ORDER BY f.report_date DESC LIMIT 1),
                           (SELECT f.pbr FROM financial_statements f
                             WHERE f.stock_code = s.stock_code
                             ORDER BY f.report_date DESC LIMIT 1)
                    FROM stocks s WHERE s.stock_code = %s
                """, (stock_code,))
            me = cur.fetchone()
            cur.close()

            if not me:
                return features
            sector = me[0]
            my_per = abs(float(me[1])) if me[1] is not None else 0.0
            my_pbr = abs(float(me[2])) if me[2] is not None else 0.0

            if my_per <= 0 and my_pbr <= 0:
                return features

            # 2) 피어 집합: 섹터가 있으면 동일 섹터, 없으면 시장 전체(최신 재무제표 기준)
            cur = db_conn.cursor()
            if sector:
                if date:
                    cur.execute("""
                        SELECT fs.per, fs.pbr
                        FROM financial_statements fs
                        JOIN stocks s ON fs.stock_code = s.stock_code
                        WHERE s.sector = %s AND fs.per > 0 AND fs.pbr > 0
                          AND fs.report_date = (
                              SELECT MAX(report_date) FROM financial_statements f2
                              WHERE f2.stock_code = fs.stock_code AND f2.report_date <= %s
                          )
                    """, (sector, date))
                else:
                    cur.execute("""
                        SELECT fs.per, fs.pbr
                        FROM financial_statements fs
                        JOIN stocks s ON fs.stock_code = s.stock_code
                        WHERE s.sector = %s AND fs.per > 0 AND fs.pbr > 0
                          AND fs.report_date = (
                              SELECT MAX(report_date) FROM financial_statements f2
                              WHERE f2.stock_code = fs.stock_code
                          )
                    """, (sector,))
            else:
                if date:
                    cur.execute("""
                        SELECT fs.per, fs.pbr
                        FROM financial_statements fs
                        WHERE fs.per > 0 AND fs.pbr > 0
                          AND fs.report_date = (
                              SELECT MAX(report_date) FROM financial_statements f2
                              WHERE f2.stock_code = fs.stock_code AND f2.report_date <= %s
                          )
                    """, (date,))
                else:
                    cur.execute("""
                        SELECT fs.per, fs.pbr
                        FROM financial_statements fs
                        WHERE fs.per > 0 AND fs.pbr > 0
                          AND fs.report_date = (
                              SELECT MAX(report_date) FROM financial_statements f2
                              WHERE f2.stock_code = fs.stock_code
                          )
                    """)
            all_rows = cur.fetchall()
            cur.close()

            if all_rows:
                per_vals = sorted([abs(r[0]) for r in all_rows if r[0]])
                pbr_vals = sorted([abs(r[1]) for r in all_rows if r[1]])

                if per_vals and my_per > 0:
                    rank = sum(1 for p in per_vals if p <= my_per)
                    features["per_percentile"] = (rank / len(per_vals)) * 100

                if pbr_vals and my_pbr > 0:
                    rank = sum(1 for p in pbr_vals if p <= my_pbr)
                    features["pbr_percentile"] = (rank / len(pbr_vals)) * 100

        except Exception as e:
            logger.debug(f"Percentile features failed for {stock_code}: {e}")
            if db_conn:
                db_conn.rollback()

        return features

    def get_all_features(self, stock_code: str, db_conn=None, date: Optional[str] = None) -> Dict:
        """Get all company fundamental features."""
        features = {}
        features.update(self.get_financial_features(stock_code, db_conn))
        features.update(self.get_percentile_features(stock_code, db_conn, date=date))
        return features
