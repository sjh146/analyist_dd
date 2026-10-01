"""Quality score calculator — F-Score from financial statements."""
import logging, psycopg2
from typing import Dict

logger = logging.getLogger(__name__)

class QualityScorer:
    """Computes quality scores from static financial data."""
    
    def get_f_score(self, stock_code: str, db_conn=None, date=None) -> float:
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

        ``date`` (YYYY-MM-DD, 선택): **시점정합 기준일**. 주면 그 시점에 알 수 있었던 보고서만
        쓴다(공시 지연 90일/45일 — factor_features.asof_report_predicate 와 같은 규칙).
        주지 않으면 종전 동작(빌드 시점 최신 행) — 기본값이라 기존 호출부는 무변경.

        왜(2026-10-02 실측): 종전에는 `ORDER BY fs.report_date DESC LIMIT 1` 로 읽어 과거 행에도
        빌드 시점 최신 재무가 들어갔다 → panel_420_asofpatch/panel_150u/panel_995 에서
        quality_score 가 **종목당 유니크값 1**(비영 100%) = 종목 상수였고, 패널 스크린의
        단일피처 AUC 2위(0.5507)가 정확히 이 컬럼이었다. 누수 게이트 ②·③ 위반.

        ⚠ 남은 한계: mcap 기준은 `stocks.market_cap`(현재값)을 그대로 쓴다 — 점수 8항 중 1항만
        영향이고 상장주식수 컬럼이 없어 as-of 환산에 종가 조회 2회가 더 필요하다(패널 빌드
        처리량에 민감). 재무 7항은 모두 as-of 로 바뀌었다.
        """
        if db_conn is None:
            return 0.5  # neutral
        
        try:
            cur = db_conn.cursor()
            if date:
                cur.execute("""
                    SELECT fs.net_income, fs.operating_profit, fs.revenue,
                           fs.total_assets, fs.total_equity, fs.roe, fs.debt_ratio,
                           COALESCE(s.market_cap, 0) as mcap
                    FROM financial_statements fs
                    LEFT JOIN stocks s ON fs.stock_code = s.stock_code
                    WHERE fs.stock_code = %s AND fs.revenue IS NOT NULL
                      AND fs.report_date + (CASE
                            WHEN fs.report_date = date_trunc('year', fs.report_date)::date
                            THEN INTERVAL '90 days' ELSE INTERVAL '45 days' END) <= %s::date
                    ORDER BY fs.report_date DESC
                    LIMIT 1
                """, (stock_code, date))
            else:
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
