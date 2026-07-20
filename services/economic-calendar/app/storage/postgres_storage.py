import logging
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

from app.config import Config

logger = logging.getLogger(__name__)


class PostgresStorage:
    def __init__(self):
        self.config = Config()
        self._conn = None
        self._init_table()

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.config.db_url)
            self._conn.autocommit = True
        return self._conn

    def _init_table(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS economic_events (
                        id SERIAL PRIMARY KEY,
                        event_date DATE NOT NULL,
                        country VARCHAR(10) NOT NULL,
                        category VARCHAR(50) NOT NULL,
                        title VARCHAR(500) NOT NULL,
                        importance VARCHAR(10) NOT NULL DEFAULT 'medium',
                        source VARCHAR(50),
                        created_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(event_date, country, title)
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_economic_events_date
                    ON economic_events(event_date)
                """)
            logger.info("economic_events table ready")
        except Exception as e:
            logger.error("Failed to init table: %s", e)

    def save_events(self, events_list: list[dict]) -> int:
        saved = 0
        for ev in events_list:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO economic_events (event_date, country, category, title, importance, source)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (event_date, country, title) DO NOTHING
                        """,
                        (
                            ev["event_date"],
                            ev["country"],
                            ev["category"],
                            ev["title"],
                            ev.get("importance", "medium"),
                            ev.get("source", ""),
                        ),
                    )
                    if cur.rowcount > 0:
                        saved += 1
            except Exception as e:
                logger.warning("Failed to save event %s: %s", ev.get("title", "?"), e)
        if saved:
            logger.info("Saved %d new events", saved)
        return saved

    def get_upcoming_events(self, days: int = 14) -> list[dict]:
        try:
            with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT event_date, country, category, title, importance, source
                    FROM economic_events
                    WHERE event_date >= CURRENT_DATE
                      AND event_date < CURRENT_DATE + INTERVAL %s
                    ORDER BY event_date, importance DESC, country
                    """,
                    (f"{days} days",),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("Failed to get upcoming events: %s", e)
            return []
