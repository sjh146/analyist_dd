"""Quality score calculator — F-Score from financial statements."""
import logging, psycopg2
from typing import Dict

logger = logging.getLogger(__name__)

class QualityScorer:
    """Computes quality scores from static financial data."""
    
    def get_f_score(self, stock_code: str, db_conn) -> float:
        """
        Compute simplified F-Score (0.0~1.0) for a stock.
        8 criteria, each worth 0.125:
        - net_income > 0
        - operating_profit > 0  
        - revenue > 0
        - total_assets > 0
        - total_equity > 0
        - roe > 0.05
        - debt_ratio < 100
        - market_cap > 1000억
        """
        if db_conn is None:
            return 0.5  # neutral
        
        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT fs.net_income, fs.operating_profit, fs.revenue,
                       fs.total_assets, fs.total_equity, fs.roe, fs.debt_ratio,
                       COALESCE(s.market_cap, 0) as mcap
                FROM financial_statements fs
                LEFT JOIN stocks s ON fs.stock_code = s.stock_code
                WHERE fs.stock_code = %s AND fs.revenue IS NOT NULL
                ORDER BY fs.report_date DESC
                LIMIT 1
            """, (stock_code,))
            row = cur.fetchone()
            cur.close()
            
            if not row:
                return 0.5  # neutral when no data
            
            ni, op, rev, assets, equity, roe, debt, mcap = [
                float(v) if v else 0.0 for v in row
            ]
            
            score = 0.0
            if ni > 0: score += 0.125
            if op > 0: score += 0.125
            if rev > 0: score += 0.125
            if assets > 0: score += 0.125
            if equity > 0: score += 0.125
            if roe and roe > 0.05: score += 0.125
            if debt is not None and debt < 100: score += 0.125
            if mcap > 100000000000: score += 0.125  # 1000억 이상
            
            return min(max(score, 0.0), 1.0)  # clamp to 0~1
            
        except Exception as e:
            logger.debug(f"F-Score failed for {stock_code}: {e}")
            if db_conn: db_conn.rollback()
            return 0.5
