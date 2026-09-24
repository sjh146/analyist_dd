"""
Vector Features
Extracts features from pgvector similarity search.
"""

import logging
import numpy as np
from typing import Dict, List, Optional

from app.feature_engine.market_data_filter import MARKET_DATA_VALID

logger = logging.getLogger(__name__)


class VectorFeatures:
    """Features derived from pgvector stock similarity search.

    실측 이력(2026-09-24)
    --------------------
    유사도 계열 4개 피처가 죽어 있었다(feature_coverage: avg_similarity_top10=1.0
    상수, max_similarity=1.0 상수, similarity_std=0.0 상수, similar_count=10 상수).
    원인은 이 모듈이 아니라 **임베딩 생성기** 였다: stock-vectorizer 의 main.py 가
    price/sentiment 벡터라이저에 시세/감성 대신 stock_code 를 넘겨 항상 영벡터를
    만들었고, 그 결과 stock_vectors 2773행 중 서로 다른 임베딩이 3개뿐이었다
    (count(DISTINCT embedding)=3, 실측). 임베딩이 같으면 코사인 유사도가 정확히
    1.0 이 되어 패널 전 종목이 상수 1.0 을 받았다.
    임베딩 재생성 후 실측 유사도가 0.897~0.995 로 퍼지므로 이 모듈도 다음을 보강한다.

    · ``similar_count``: 종전에는 조회 결과 수 = top_k 라서 **항상 10** 이었다.
      이제 후보 풀(``CANDIDATE_POOL``=300)에서 코사인 유사도 >= ``SIMILARITY_THRESHOLD``
      인 종목 수를 센다(실제 "비슷한 종목이 몇 개인가"를 나타낸다).
    · ``date``: 유사 종목의 5일 수익률을 date 기준으로 조회한다(룩어헤드 제거).
      date=None 이면 현행(최신 데이터) 동작을 그대로 유지한다.
    """

    #: similar_count 로 셀 최소 코사인 유사도.
    #  실측(2026-09-24, 재생성 후 15종목 표본): 상위 50위 이웃의 코사인 유사도가
    #  0.96~0.999 로 매우 높아 0.90/0.95 임계값은 전 종목을 포화시킨다(상수 50).
    #  0.99 로 두면 종목별 개수가 0 ~ 189 로 갈린다 → 살아있는 피처가 된다.
    SIMILARITY_THRESHOLD = 0.99
    #: similar_count 계산용 후보 풀 크기 (>= top_k). 하나의 SQL 로 가져오므로
    #  풀을 키워도 종목별 추가 조회는 없다(여기 300 = 상한이 거의 안 걸리는 크기).
    CANDIDATE_POOL = 300

    def get_similar_stock_features(
        self, stock_code: str, db_conn=None, top_k: int = 10, date: Optional[str] = None
    ) -> Dict:
        """Get features from top-K similar stocks via pgvector cosine similarity.

        Args:
            stock_code: 기준 종목.
            db_conn: psycopg2 연결.
            top_k: 유사도 통계(avg/max/std)를 계산할 상위 K.
            date: 기준일(YYYY-MM-DD). 유사 종목의 5일 수익률을 이 날짜까지로 제한한다.
        """
        features = {
            "avg_similarity_top10": 0.0, "max_similarity": 0.0,
            "similarity_std": 0.0, "similar_count": 0,
            "similar_stocks_return_avg": 0.0, "similar_stocks_return_std": 0.0,
        }

        if db_conn is None:
            return features

        try:
            pool = max(top_k, self.CANDIDATE_POOL)
            similar = self._find_similar_stocks(stock_code, db_conn, pool)
            if not similar:
                return features

            top = similar[:top_k]
            # 5일 수익률은 유사도 통계(top_k)에만 쓰므로 상위 K 개만 조회한다
            # (후보 풀 50개 전부에 대해 종목별 조회를 하면 DB 왕복이 5배가 된다).
            for s in top:
                s["return_5d"] = self._get_return_5d(s["stock_code"], db_conn, date=date)

            similarities = [
                float(s["similarity"])
                for s in top
                if s.get("similarity") is not None and np.isfinite(s["similarity"])
            ]
            if not similarities:
                return features

            features["avg_similarity_top10"] = float(np.mean(similarities))
            features["max_similarity"] = float(np.max(similarities))
            features["similarity_std"] = (
                float(np.std(similarities)) if len(similarities) > 1 else 0.0
            )
            # 상수 10(top_k) 이 되지 않도록 임계값 이상만 센다.
            features["similar_count"] = int(
                sum(
                    1 for s in similar
                    if s.get("similarity") is not None
                    and np.isfinite(s["similarity"])
                    and s["similarity"] >= self.SIMILARITY_THRESHOLD
                )
            )

            returns = [
                s.get("return_5d")
                for s in top
                if s.get("return_5d") is not None
            ]
            if returns:
                features["similar_stocks_return_avg"] = float(np.mean(returns))
                features["similar_stocks_return_std"] = float(np.std(returns)) if len(returns) > 1 else 0.0

        except Exception as e:
            logger.debug(f"Vector features failed for {stock_code}: {e}")
            if db_conn:
                db_conn.rollback()

        return features

    def _find_similar_stocks(
        self, stock_code: str, db_conn, top_k: int
    ) -> List[Dict]:
        """Query pgvector for similar stocks by cosine distance.

        반환 항목의 ``return_5d`` 는 채우지 않는다(호출자가 필요한 만큼만 조회).
        """
        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT sv2.stock_code,
                       1 - (sv2.embedding <=> sv1.embedding) as similarity
                FROM stock_vectors sv1
                JOIN stock_vectors sv2 ON sv1.vector_type = sv2.vector_type
                WHERE sv1.stock_code = %s
                  AND sv2.stock_code != %s
                  AND sv1.vector_type = 'combined'
                  AND sv1.embedding IS NOT NULL
                  AND sv2.embedding IS NOT NULL
                ORDER BY sv2.embedding <=> sv1.embedding
                LIMIT %s
            """, (stock_code, stock_code, top_k))
            rows = cur.fetchall()
            cur.close()

            similar = []
            for row in rows:
                sim_score = float(row[1]) if row[1] is not None else 0.0
                similar.append({
                    "stock_code": row[0],
                    "similarity": sim_score,
                    "return_5d": None,
                })
            return similar

        except Exception as e:
            logger.debug(f"Similarity query failed: {e}")
            if db_conn:
                db_conn.rollback()
            return []

    def _get_return_5d(
        self, stock_code: str, db_conn, date: Optional[str] = None
    ) -> Optional[float]:
        """Get 5-day return for a stock from market_data.

        date 가 주어지면 trade_date <= date 인 최근 6개 종가로 계산한다(룩어헤드 방지).
        """
        try:
            cur = db_conn.cursor()
            if date:
                cur.execute(f"""
                    SELECT close_price
                    FROM market_data
                    WHERE stock_code = %s AND trade_date <= %s
                      AND {MARKET_DATA_VALID}
                    ORDER BY trade_date DESC
                    LIMIT 6
                """, (stock_code, date))
            else:
                cur.execute(f"""
                    SELECT close_price
                    FROM market_data
                    WHERE stock_code = %s
                      AND {MARKET_DATA_VALID}
                    ORDER BY trade_date DESC
                    LIMIT 6
                """, (stock_code,))
            rows = cur.fetchall()
            cur.close()

            if len(rows) >= 6 and rows[5][0]:
                return float(rows[0][0] / rows[5][0] - 1)
            return 0.0
        except Exception:
            if db_conn:
                db_conn.rollback()
            return None

    def get_vector_features_from_db(
        self, stock_code: str, db_conn=None, date: Optional[str] = None
    ) -> Dict:
        """Get all vector similarity features.

        Args:
            date: 기준일(YYYY-MM-DD). 미지정(None)이면 현행과 동일하게 최신 데이터 사용.
        """
        return self.get_similar_stock_features(stock_code, db_conn, date=date)
