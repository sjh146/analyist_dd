"""
PostgreSQL Storage for News/SNS Analyzer
Handles inserting and updating analysis results.

get_stock_by_name 종목명 매칭 정규화 (2026-11-19, 스펙 docs/spec_news_coverage_fix.md §1):
WHY 는 get_stock_by_name docstring 참고. 실측 근거(2026-11-19, 조회 명령 원문):

    docker exec stock_postgres psql -U stock_user -d stock_trading \
      -c "SELECT count(*), count(DISTINCT stock_name) FROM stocks"
    → 4,352행 / 4,352개 (이름 중복 0)
    -c "SELECT count(*) FROM stocks WHERE stock_name LIKE '%(주)%' OR stock_name LIKE '%㈜%' OR stock_name LIKE '%주식회사%'"
    → 0행 (suffix 제거는 입력 쪽에서만 의미)
    -c "SELECT count(*) FROM stocks WHERE stock_name = '채권혼합'"
    → 0 (부분일치 41건 → 모호 실패 테스트 케이스)
    -c "SELECT upper(replace(replace(replace(replace(stock_name,' ',''),'(주)',''),'㈜',''),'주식회사','')) AS norm, count(*) FROM stocks GROUP BY 1 HAVING count(*)>1"
    → 0행 (정규화 키 충돌 0 → 정규화 equality 히트는 항상 ≤ 1)
    -c "\\d stocks"
    → stock_name 에 인덱스 없음 → 어떤 이름 매칭 SQL 도 시퀀스 스캔.
      get_stock_by_name 은 stocks 전체를 1회 조회해 Python 에서 ①~④ 를 수행한다.
"""

import psycopg2
import psycopg2.pool
import json
import logging
from datetime import date, datetime
from typing import Optional, Dict, List

from app.config import Config
from app.models.schemas import Article, AnalysisResult, StockSentiment, StructuredNews
from app.events.clusterer import EventCluster
from app.thesis.thesis_verifier import ThesisVerdict

logger = logging.getLogger(__name__)


def normalize_stock_name(name) -> str:
    """종목명 정규화 키 (스펙 §1-②): 공백·(주)·㈜·주식회사 제거 후 대문자.

    정확히 스펙에 나열된 항목만 제거한다. DB 쪽 stocks.stock_name 에는
    '(주)' 등이 0건(실측)이라 사실상 입력 쪽 정규화로만 효과가 있다.
    """
    if not name:
        return ""
    return (
        str(name)
        .replace(" ", "")
        .replace("(주)", "")
        .replace("㈜", "")
        .replace("주식회사", "")
        .upper()
    )


class PostgresStorage:
    def __init__(self):
        self.config = Config()
        self._pool = None
        self._stock_name_tables_ready = False
        self._init_pool()

    def _init_pool(self):
        """Initialize connection pool."""
        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                host=self.config.POSTGRES_HOST,
                port=self.config.POSTGRES_PORT,
                dbname=self.config.POSTGRES_DB,
                user=self.config.POSTGRES_USER,
                password=self.config.POSTGRES_PASSWORD,
            )
            logger.info("PostgreSQL connection pool initialized")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL pool: {e}")

    def _get_conn(self):
        """Get connection from pool."""
        if not self._pool:
            return None
        try:
            return self._pool.getconn()
        except Exception as e:
            logger.error(f"Failed to get connection from pool: {e}")
            return None

    def _put_conn(self, conn):
        """Return connection to pool."""
        if self._pool and conn:
            self._pool.putconn(conn)

    def save_news_analysis(self, article: Article, result: AnalysisResult):
        """Insert news analysis into PostgreSQL."""
        conn = self._get_conn()
        if not conn:
            logger.error("No DB connection available")
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO news_analysis
                    (source, title, content, url, published_at,
                     authenticity_score, authenticity_label,
                     sentiment_score, sentiment_label, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                """,
                (
                    article.source,
                    article.title,
                    article.content[:5000] if article.content else None,
                    article.url,
                    article.published_at,
                    result.authenticity_score,
                    result.authenticity_label,
                    result.sentiment_score,
                    result.sentiment_label,
                    result.confidence,
                ),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save news analysis: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_event_extraction(self, article_id: int, structured: StructuredNews):
        """Insert structured event extraction (JSONB + article FK).

        article_id는 news_analysis 저장 후 해당 행의 id. stock_code는
        이미 stocks 테이블에 존재하는 유효 코드만 전달된다(관계 무단 생성 방지).
        """
        conn = self._get_conn()
        if not conn:
            logger.error("No DB connection available")
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO news_event_extraction
                    (article_id, stock_code, event_type, themes,
                     sentiment_score, importance, novelty, time_range,
                     core_event_text, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    article_id,
                    structured.stock_code,
                    structured.event_type,
                    json.dumps(structured.themes, ensure_ascii=False),
                    structured.sentiment_score,
                    structured.importance,
                    structured.novelty,
                    structured.time_range,
                    structured.core_event_text,
                    json.dumps(
                        {
                            "stock_code": structured.stock_code,
                            "event_type": structured.event_type,
                            "themes": structured.themes,
                            "sentiment_score": structured.sentiment_score,
                            "importance": structured.importance,
                            "novelty": structured.novelty,
                            "time_range": structured.time_range,
                            "core_event_text": structured.core_event_text,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save event extraction: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_event_cluster(self, cluster: EventCluster):
        """Upsert an event cluster into news_events (Phase 3).

        embedding vector(384) 컬럼은 Phase 4에서 채워진다. Phase 3에서는 저장하지 않는다.
        """
        conn = self._get_conn()
        if not conn:
            logger.error("No DB connection available")
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO news_events
                    (stock_code, event_type, event_date, time_bucket,
                     cluster_key, article_count, first_article_at,
                     last_article_at, total_importance, max_sentiment_abs,
                     representative_core_event_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cluster_key) DO UPDATE SET
                    stock_code = EXCLUDED.stock_code,
                    event_type = EXCLUDED.event_type,
                    event_date = EXCLUDED.event_date,
                    time_bucket = EXCLUDED.time_bucket,
                    article_count = EXCLUDED.article_count,
                    first_article_at = EXCLUDED.first_article_at,
                    last_article_at = EXCLUDED.last_article_at,
                    total_importance = EXCLUDED.total_importance,
                    max_sentiment_abs = EXCLUDED.max_sentiment_abs,
                    representative_core_event_text = EXCLUDED.representative_core_event_text
                """,
                (
                    cluster.stock_code,
                    cluster.event_type,
                    cluster.event_date,
                    cluster.time_bucket,
                    cluster.cluster_key,
                    cluster.article_count,
                    cluster.first_article_at,
                    cluster.last_article_at,
                    cluster.total_importance,
                    cluster.max_sentiment_abs,
                    cluster.representative_core_event_text,
                ),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save event cluster: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def update_event_embedding(self, cluster_key: str, embedding) -> None:
        """Store a 384-dim embedding vector for an event cluster (Phase 4).

        ``embedding`` is a numpy array or list of 384 floats. Fail-open: on
        error the cluster row is left unchanged.
        """
        conn = self._get_conn()
        if not conn:
            logger.error("No DB connection available")
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE news_events
                SET embedding = %s
                WHERE cluster_key = %s
                """,
                # numpy.float32는 psycopg2가 adapt 불가 → Python float로 변환
                ([float(x) for x in embedding], cluster_key),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to update event embedding for {cluster_key}: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def search_similar_events(self, embedding, limit: int = 10) -> List[Dict]:
        """Find the most similar event clusters by cosine distance (Phase 4).

        Uses the HNSW index on ``news_events.embedding`` via
        ``ORDER BY embedding <=> $q LIMIT n``. Returns rows with similarity
        (1 - cosine distance).
        """
        conn = self._get_conn()
        if not conn:
            return []

        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT stock_code, event_type, event_date, cluster_key,
                       representative_core_event_text,
                       1 - (embedding <=> %s) AS similarity
                FROM news_events
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (list(embedding), list(embedding), limit),
            )
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "stock_code": r[0],
                    "event_type": r[1],
                    "event_date": r[2],
                    "cluster_key": r[3],
                    "representative_core_event_text": r[4],
                    "similarity": r[5],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to search similar events: {e}")
            return []
        finally:
            self._put_conn(conn)

    def _ensure_validated_at_column(self):
        """Ensure validated_at column exists on stock_sentiment."""
        conn = self._get_conn()
        if not conn:
            return False
        try:
            cur = conn.cursor()
            cur.execute(
                "ALTER TABLE stock_sentiment ADD COLUMN IF NOT EXISTS validated_at TIMESTAMP"
            )
            conn.commit()
            cur.close()
            return True
        except Exception as e:
            logger.warning(
                f"Cannot add validated_at column: {e}. "
                "Run: ALTER TABLE stock_sentiment ADD COLUMN validated_at TIMESTAMP;"
            )
            return False
        finally:
            self._put_conn(conn)

    def save_stock_sentiment(
        self,
        stock_code: str,
        date: datetime.date,
        sentiment_score: float,
        is_news: bool = True,
        validated_at: Optional[datetime] = None,
    ):
        """Upsert stock sentiment data."""
        conn = self._get_conn()
        if not conn:
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO stock_sentiment
                    (stock_code, analysis_date, avg_sentiment,
                     sentiment_count, news_count,
                     positive_count, negative_count, neutral_count,
                     validated_at)
                VALUES (%s, %s, %s, 1, %s,
                        CASE WHEN %s > 0.2 THEN 1 ELSE 0 END,
                        CASE WHEN %s < -0.2 THEN 1 ELSE 0 END,
                        CASE WHEN %s >= -0.2 AND %s <= 0.2 THEN 1 ELSE 0 END,
                        %s)
                ON CONFLICT (stock_code, analysis_date) DO UPDATE SET
                    avg_sentiment = (stock_sentiment.avg_sentiment * stock_sentiment.sentiment_count + %s)
                                    / (stock_sentiment.sentiment_count + 1),
                    sentiment_count = stock_sentiment.sentiment_count + 1,
                    news_count = stock_sentiment.news_count + CASE WHEN %s THEN 1 ELSE 0 END,
                    positive_count = stock_sentiment.positive_count + CASE WHEN %s > 0.2 THEN 1 ELSE 0 END,
                    negative_count = stock_sentiment.negative_count + CASE WHEN %s < -0.2 THEN 1 ELSE 0 END,
                    neutral_count = stock_sentiment.neutral_count + CASE WHEN %s >= -0.2 AND %s <= 0.2 THEN 1 ELSE 0 END
                """,
                (
                    stock_code,
                    date,
                    sentiment_score,
                    1 if is_news else 0,
                    sentiment_score,
                    sentiment_score,
                    sentiment_score,
                    sentiment_score,
                    validated_at,
                    sentiment_score,
                    is_news,
                    sentiment_score,
                    sentiment_score,
                    sentiment_score,
                    sentiment_score,
                ),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save stock sentiment: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def get_recent_event_extractions(self, since: datetime) -> List[Dict]:
        """Read recent news_event_extraction rows for clustering (Phase 3)."""
        conn = self._get_conn()
        if not conn:
            return []

        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT stock_code, event_type, created_at, importance,
                       sentiment_score, core_event_text
                FROM news_event_extraction
                WHERE created_at >= %s
                """,
                (since,),
            )
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "stock_code": r[0],
                    "event_type": r[1],
                    "created_at": r[2],
                    "importance": r[3],
                    "sentiment_score": r[4],
                    "core_event_text": r[5],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to read event extractions: {e}")
            return []
        finally:
            self._put_conn(conn)

    def get_analysis_by_url(self, url: str) -> Optional[Dict]:
        """Check if a URL has already been analyzed."""
        conn = self._get_conn()
        if not conn:
            return None

        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, analyzed_at FROM news_analysis WHERE url = %s",
                (url,),
            )
            row = cur.fetchone()
            cur.close()
            if row:
                return {"id": row[0], "analyzed_at": row[1]}
            return None
        except Exception as e:
            logger.error(f"Failed to check analysis: {e}")
            return None
        finally:
            self._put_conn(conn)

    def get_stock_by_name(self, name: str) -> Optional[Dict]:
        """Search stock by name — 정규화 캐스케이드 매칭 (스펙 §1).

        WHY (2026-11-19 실측): news_events 12,828행 중 6,854행(53.4%)이
        stock_code IS NULL 이었고, 원인 중 하나가 표기 차이("삼성전자" vs
        "삼성전자(주)", "SK하이닉스" vs "SK 하이닉스")에 의한 정확일치 실패였다
        (docs/spec_news_coverage_fix.md 원인분석). 종전 구현은
        ``stock_name LIKE %name%`` + fetchone() 하나였기 때문에, 실패는 조용히
        None 으로 남아 어떤 이름이 실패했는지 관측조차 불가능했다.

        매칭 순서(앞 단계에서 성공하면 종료):
          ① 원문 정확일치 (stock_name = 입력 그대로)
          ② 정규화 equality — 공백·(주)·㈜·주식회사 제거 + 대문자
             (실측: 정규화 키 4,352개 전부 distinct → 히트는 0 또는 1행)
          ③ 별칭 테이블 stock_name_aliases(alias_norm PK) 조회
          ④ 정규화 부분일치 — 히트가 **정확히 1건일 때만** 성공.
             2건 이상이면 오매핑 위험이므로 실패로 남긴다(스펙 §5 금지 조항).
        실패 시 news_stock_match_failures 에 upsert(hits += 1) 로 실패 이름을
        관측 가능하게 남긴다. 반환 계약은 기존과 동일: 실패 시 None —
        main.py::_save_structured_event 는 stock_code=None 으로 이벤트를
        저장한다(관계 무단 생성 방지).

        stocks 전체(4,352행)를 1회 조회해 Python 에서 매칭한다: stock_name 에
        인덱스가 없어(\\d stocks 실측) 어떤 SQL 매칭이든 시퀀스 스캔이므로,
        단계별 3회 스캔 대신 1회 스캔이 더 빠르고, 정규화 키도 Python 한 곳에서
        정의되어 SQL 식과 어긋날 여지가 없다.
        """
        name = (name or "").strip()
        if not name:
            return None
        if not self._stock_name_tables_ready:
            self._stock_name_tables_ready = self._ensure_stock_name_tables()

        conn = self._get_conn()
        if not conn:
            return None

        try:
            cur = conn.cursor()
            cur.execute("SELECT stock_code, stock_name, sector FROM stocks")
            stocks = cur.fetchall()
            cur.close()

            # ① 원문 정확일치
            for code, stock_name, sector in stocks:
                if stock_name == name:
                    return {
                        "stock_code": code,
                        "stock_name": stock_name,
                        "sector": sector,
                    }

            norm = normalize_stock_name(name)
            if not norm:
                # 이름이 전부 제거 대상 문자뿐인 경우(예: "㈜") — 부분일치 불가
                self._record_match_failure(name, norm, "no_match")
                return None

            # ② 정규화 equality
            norm_hits = [
                (c, n, s)
                for c, n, s in stocks
                if normalize_stock_name(n) == norm
            ]
            if len(norm_hits) == 1:
                c, n, s = norm_hits[0]
                return {"stock_code": c, "stock_name": n, "sector": s}
            if len(norm_hits) >= 2:
                # 실측상 0건이지만(정규화 키 충돌 0) 오매핑 금지 원칙 유지
                self._record_match_failure(name, norm, "ambiguous")
                return None

            # ③ 별칭 테이블 (alias_norm = 정규화 키)
            cur = conn.cursor()
            cur.execute(
                "SELECT stock_code FROM stock_name_aliases WHERE alias_norm = %s",
                (norm,),
            )
            alias = cur.fetchone()
            cur.close()
            if alias:
                by_code = {c: (n, s) for c, n, s in stocks}
                hit = by_code.get(alias[0])
                if hit:
                    return {
                        "stock_code": alias[0],
                        "stock_name": hit[0],
                        "sector": hit[1],
                    }

            # ④ 정규화 부분일치 — 정확히 1건일 때만 성공
            partial = [
                (c, n, s)
                for c, n, s in stocks
                if norm in normalize_stock_name(n)
            ]
            if len(partial) == 1:
                c, n, s = partial[0]
                return {"stock_code": c, "stock_name": n, "sector": s}
            if len(partial) >= 2:
                self._record_match_failure(name, norm, "ambiguous")
                return None

            # 0건 — 실패 기록 후 None (기존 계약)
            self._record_match_failure(name, norm, "no_match")
            return None
        except Exception as e:
            logger.error(f"Failed to search stock: {e}")
            return None
        finally:
            self._put_conn(conn)

    def _ensure_stock_name_tables(self) -> bool:
        """매칭 보조 테이블 생성 (멱등 — IF NOT EXISTS, 재실행 안전).

        - stock_name_aliases(alias_norm PK, stock_code, created_at): 별칭 → 코드.
        - news_stock_match_failures(name_raw PK, name_norm, hits, last_reason,
          created_at, updated_at): 실패 이름 원장. 2026-11-19 \\dt 실측으로
          이미 DB 에 존재(0행) — CREATE IF NOT EXISTS 는 no-op.
        """
        conn = self._get_conn()
        if not conn:
            return False
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_name_aliases (
                    alias_norm TEXT PRIMARY KEY,
                    stock_code VARCHAR(10) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS news_stock_match_failures (
                    name_raw    TEXT PRIMARY KEY,
                    name_norm   TEXT,
                    hits        INTEGER NOT NULL DEFAULT 0,
                    last_reason TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.commit()
            cur.close()
            return True
        except Exception as e:
            logger.warning(f"Cannot ensure stock name match tables: {e}")
            return False
        finally:
            self._put_conn(conn)

    def _record_match_failure(self, name_raw: str, name_norm: str, reason: str):
        """실패 이름 upsert (스펙 §1-2) — hits += 1, 같은 원문은 1행 유지(멱등).

        3회 도달 시점에만 WARNING 1회(교차 시점 로깅 → 중복 억제, 스펙 §1-2).
        이 기록 자체의 오류는 삼킨다 — 매칭 실패 시 None 반환 계약을 깨지 않는다.
        """
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO news_stock_match_failures
                    (name_raw, name_norm, hits, last_reason)
                VALUES (%s, %s, 1, %s)
                ON CONFLICT (name_raw) DO UPDATE SET
                    hits = news_stock_match_failures.hits + 1,
                    name_norm = EXCLUDED.name_norm,
                    last_reason = EXCLUDED.last_reason,
                    updated_at = now()
                RETURNING hits
                """,
                (name_raw, name_norm, reason),
            )
            total_hits = cur.fetchone()[0]
            conn.commit()
            cur.close()
            if total_hits == 3:
                logger.warning(
                    f"Stock name match failed {total_hits}+ times: "
                    f"'{name_raw}' (norm='{name_norm}', reason={reason})"
                )
        except Exception as e:
            logger.error(f"Failed to record stock match failure: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def get_active_theses(self) -> List[Dict]:
        """Read active position_theses rows (thesis ledger)."""
        conn = self._get_conn()
        if not conn:
            return []

        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, stock_code, thesis_text, disproof_criteria, catalyst_events
                FROM position_theses
                WHERE status = 'active'
                """
            )
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "id": r[0],
                    "stock_code": r[1],
                    "thesis_text": r[2],
                    "disproof_criteria": r[3],
                    "catalyst_events": r[4],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to read active theses: {e}")
            return []
        finally:
            self._put_conn(conn)

    def get_stock_events(self, stock_code: str, since: datetime) -> List[Dict]:
        """Read news_event_extraction rows since midnight (verdict evidence)."""
        conn = self._get_conn()
        if not conn:
            return []

        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, event_type, sentiment_score, importance, core_event_text
                FROM news_event_extraction
                WHERE stock_code = %s AND created_at >= %s
                """,
                (stock_code, since),
            )
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "id": r[0],
                    "event_type": r[1],
                    "sentiment_score": r[2],
                    "importance": r[3],
                    "core_event_text": r[4],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to read stock events for {stock_code}: {e}")
            return []
        finally:
            self._put_conn(conn)

    def get_extra_context(self, stock_code: str, verdict_date: date) -> Optional[Dict]:
        """Sentiment row + recent closes for the judge prompt — None when both absent."""
        conn = self._get_conn()
        if not conn:
            return None

        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT analysis_date, avg_sentiment, sentiment_count,
                       positive_count, negative_count
                FROM stock_sentiment
                WHERE stock_code = %s AND analysis_date <= %s
                ORDER BY analysis_date DESC
                LIMIT 1
                """,
                (stock_code, verdict_date),
            )
            sent = cur.fetchone()
            cur.execute(
                """
                SELECT trade_date, close_price
                FROM market_data
                WHERE stock_code = %s AND trade_date <= %s
                ORDER BY trade_date DESC
                LIMIT 5
                """,
                (stock_code, verdict_date),
            )
            closes = cur.fetchall()
            cur.close()

            def _pct(a, b) -> Optional[float]:
                if a is None or b is None:
                    return None
                fa, fb = float(a), float(b)
                return (fa - fb) / fb if fb else None

            if sent is None and not closes:
                return None
            sentiment = None
            if sent:
                sentiment = {
                    "analysis_date": sent[0],
                    "avg_sentiment": float(sent[1]) if sent[1] is not None else None,
                    "sentiment_count": sent[2],
                }
            return {
                "sentiment": sentiment,
                "price_change_1d": _pct(closes[0][1], closes[1][1]) if len(closes) >= 2 else None,
                "price_change_5d": _pct(closes[0][1], closes[-1][1]) if len(closes) >= 5 else None,
            }
        except Exception as e:
            logger.error(f"Failed to read extra context for {stock_code}: {e}")
            return None
        finally:
            self._put_conn(conn)

    def has_thesis_verdict(self, thesis_id: int, verdict_date: date) -> bool:
        """True if a verdict already exists for (thesis_id, verdict_date)."""
        conn = self._get_conn()
        if not conn:
            return False

        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM thesis_verdicts WHERE thesis_id = %s AND verdict_date = %s",
                (thesis_id, verdict_date),
            )
            row = cur.fetchone()
            cur.close()
            return row is not None
        except Exception as e:
            logger.error(f"Failed to check thesis verdict: {e}")
            return False
        finally:
            self._put_conn(conn)

    def get_recent_thesis_verdicts(self, thesis_id: int, limit: int = 5) -> List[Dict]:
        """최근 판정 이력 (verdict_date 내림차순) — 연속 손상 감지 등 편향 모니터링용.

        append-only 원장 읽기 전용 (INSERT/UPDATE/DELETE 경로 0). 실패 시 [] (fail-open).
        """
        conn = self._get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT thesis_id, verdict_date, verdict, verdict_score,
                       evidence_summary, model_version
                FROM thesis_verdicts
                WHERE thesis_id = %s
                ORDER BY verdict_date DESC
                LIMIT %s
                """,
                (thesis_id, limit),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in rows]
            cur.close()
            return rows
        except Exception as e:
            logger.error(f"Failed to fetch recent thesis verdicts: {e}")
            return []
        finally:
            self._put_conn(conn)

    def save_thesis_verdict(self, verdict: ThesisVerdict) -> bool:
        """Append a verdict to the append-only ledger (idempotent per day)."""
        conn = self._get_conn()
        if not conn:
            logger.error("No DB connection available")
            return False

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO thesis_verdicts (thesis_id, verdict_date, verdict, verdict_score,
                    evidence_event_ids, evidence_summary, model_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (thesis_id, verdict_date) DO NOTHING
                """,
                (
                    verdict.thesis_id,
                    verdict.verdict_date,
                    verdict.verdict,
                    verdict.verdict_score,
                    verdict.evidence_event_ids,
                    verdict.evidence_summary,
                    verdict.model_version,
                ),
            )
            conn.commit()
            cur.close()
            return True
        except Exception as e:
            logger.error(f"Failed to save thesis verdict: {e}")
            conn.rollback()
            return False
        finally:
            self._put_conn(conn)

    def close(self):
        """Close all connections."""
        if self._pool:
            self._pool.closeall()
            logger.info("PostgreSQL connection pool closed")
