"""
Graph Features
Extracts features from Neo4j graph relationships.

2026-09-24 실측 정리(테마·트윈·섹터 부활):
  * 뉴스 그래프 라이터(services/news-analyzer/app/graph/news_graph_writer.py)는
    ``(Stock)-[:HAS_THEME]->(Theme)`` 를 쓰는데 이 리더만 ``PART_OF_THEME`` 를 읽어
    두 스키마가 어긋나 있었다(실측: HAS_THEME 563, PART_OF_THEME 0 → theme_* 상수 0).
    → 두 관계를 모두 읽는다.
  * TWIN_OF / BELONGS_TO / Cycle 관계 자체가 그래프에 없었다.
    → ``scripts/feature_revival_graph_backfill.py`` 로 채우고, 리더는 없으면 0.0 으로
      안전하게 퇴화한다.
  * ``date`` 는 **선택 인자(기본 None=현행 동작)** 이다. 날짜에 의존하는
    theme_momentum / cycle_up / cycle_down 은 date 가 있어야 의미가 생긴다.
    feature_pipeline 의 호출부에 date 를 넘기는 1줄 패치가 필요하다:
        features.update(self.graph.get_graph_features(stock_code, self.neo4j_conn, date))
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class GraphFeatures:
    """Features derived from Neo4j graph: sector, theme, twin, cycle relationships."""

    @staticmethod
    def _rows(driver, query: str, **params) -> List[dict]:
        """드라이버/세션 어느 쪽이든 안전하게 질의한다.

        WHY(실측 2026-09-24): feature_pipeline 은 ``GraphDatabase.driver(...)`` 가 돌려준
        ``BoltDriver`` 객체를 그대로 넘기는데, neo4j 5.x 의 BoltDriver 에는 ``run()``
        메서드가 **없다**(AttributeError) → 이 모듈의 모든 질의가 예외로 삼켜져
        theme/twin/sector/cycle 이 **구조적으로 항상 0** 이었다.
        """
        if hasattr(driver, "run"):
            result = driver.run(query, **params)
            return [dict(r) for r in result]
        session = driver.session()
        try:
            return [dict(r) for r in session.run(query, **params)]
        finally:
            try:
                session.close()
            except Exception:
                pass

    def get_graph_features(
        self, stock_code: str, neo4j_conn=None, date: Optional[str] = None
    ) -> Dict:
        """Get all graph-based features from Neo4j.

        ``date`` (YYYY-MM-DD, optional): 시점 의존 피처(theme_momentum, cycle_*)의
        기준일. None 이면 시점 의존 피처는 0.0 을 반환한다(기존 동작 유지).
        """
        features = {
            "sector_count": 0, "theme_count": 0,
            "theme_max_relevance": 0.0, "twin_count": 0,
            "twin_avg_correlation": 0.0,
            "cycle_up": 0, "cycle_down": 0,
            "sector_momentum": 0.0, "theme_momentum": 0.0,
        }

        if neo4j_conn is None:
            return features

        try:
            features.update(self._get_sector_features(stock_code, neo4j_conn))
            features.update(self._get_theme_features(stock_code, neo4j_conn, date))
            features.update(self._get_twin_features(stock_code, neo4j_conn))
            features.update(self._get_cycle_features(stock_code, neo4j_conn, date))
        except Exception as e:
            logger.debug(f"Graph features failed for {stock_code}: {e}")

        return features

    def _get_sector_features(self, stock_code: str, driver) -> Dict:
        """Count sector relationships."""
        features = {"sector_count": 0, "sector_momentum": 0.0}
        try:
            rows = self._rows(driver, """
                MATCH (s:Stock {code: $code})-[:BELONGS_TO]->(sec:Sector)
                RETURN count(DISTINCT sec) as cnt
            """, code=stock_code)
            if rows and rows[0].get("cnt"):
                features["sector_count"] = int(rows[0]["cnt"])
        except Exception as e:
            logger.debug("sector features failed for %s: %s", stock_code, e)
        return features

    def _get_theme_features(
        self, stock_code: str, driver, date: Optional[str] = None
    ) -> Dict:
        """Count theme memberships, max relevance, and theme momentum.

        HAS_THEME(뉴스 그래프 라이터)과 PART_OF_THEME(구 스키마)를 모두 지원한다.
        relevance/count/history 는 백필 스크립트가 채운다. 하나도 없으면 theme_count>=1
        이라도 max_relevance 가 0 이 되지 않도록 균등분배(1/테마수)로 퇴화시킨다.
        """
        features = {"theme_count": 0, "theme_max_relevance": 0.0, "theme_momentum": 0.0}
        try:
            rows = self._rows(driver, """
                MATCH (s:Stock {code: $code})-[r:HAS_THEME|PART_OF_THEME]->(t:Theme)
                RETURN count(DISTINCT t) AS cnt,
                       max(coalesce(r.relevance, -1.0)) AS max_rel,
                       collect({rel: coalesce(r.relevance, -1.0),
                                hist: coalesce(r.history, '')}) AS members
            """, code=stock_code)
            if not rows:
                return features
            rec = rows[0]
            cnt = int(rec.get("cnt") or 0)
            features["theme_count"] = cnt
            if cnt > 0:
                max_rel = rec.get("max_rel")
                features["theme_max_relevance"] = (
                    float(max_rel) if max_rel is not None and float(max_rel) >= 0
                    else 1.0 / cnt
                )
                if date:
                    features["theme_momentum"] = self._theme_momentum(rec.get("members"), date)
        except Exception as e:
            logger.debug("theme features failed for %s: %s", stock_code, e)
        return features

    @staticmethod
    def _theme_momentum(members: List[Dict], date: str) -> float:
        """(최근 5일 언급수 − 직전 5일 언급수) / (합 + 1) — 종목의 테마 관심도 변화.

        history 는 ``{"YYYY-MM-DD": 언급수}`` JSON 문자열(백필 산출물).
        """
        try:
            anchor = datetime.strptime(date[:10], "%Y-%m-%d").date()
        except Exception:
            return 0.0
        recent_start = anchor - timedelta(days=4)
        prior_start = anchor - timedelta(days=9)
        recent = 0
        prior = 0
        for m in members or []:
            raw = m.get("hist") if isinstance(m, dict) else None
            if not raw:
                continue
            try:
                hist = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                continue
            if not isinstance(hist, dict):
                continue
            for d, n in hist.items():
                try:
                    dd = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
                except Exception:
                    continue
                if recent_start <= dd <= anchor:
                    recent += int(n)
                elif prior_start <= dd < recent_start:
                    prior += int(n)
        total = recent + prior
        if total == 0:
            return 0.0
        return float((recent - prior) / (total + 1))

    def _get_twin_features(self, stock_code: str, driver) -> Dict:
        """Count twin pairs and average correlation."""
        features = {"twin_count": 0, "twin_avg_correlation": 0.0}
        try:
            rows = self._rows(driver, """
                MATCH (s:Stock {code: $code})-[r:TWIN_OF]-(:Stock)
                RETURN count(r) as cnt, avg(r.correlation) as avg_corr
            """, code=stock_code)
            if rows:
                rec = rows[0]
                features["twin_count"] = int(rec["cnt"]) if rec.get("cnt") else 0
                features["twin_avg_correlation"] = (
                    float(rec["avg_corr"]) if rec.get("avg_corr") else 0.0
                )
        except Exception as e:
            logger.debug("twin features failed for %s: %s", stock_code, e)
        return features

    def _get_cycle_features(
        self, stock_code: str, driver, date: Optional[str] = None
    ) -> Dict:
        """시장 사이클 국면(one-hot).

        두 경로를 지원한다.
          (a) 종목별 ``(Stock)-[:FOLLOWS_CYCLE]->(Cycle {phase})`` — 구 스키마(시점 불변)
          (b) 일별 ``Cycle {date, phase}`` 노드 — 백필 스크립트가 시장 국면을 기록한 것.
              date 가 주어졌을 때만 읽는다(그래서 feature_pipeline 패치가 필요).
        """
        features = {"cycle_up": 0, "cycle_down": 0}
        try:
            if date:
                for rec in self._rows(driver, """
                    MATCH (c:Cycle {date: $date})
                    RETURN c.phase AS phase
                """, date=str(date)[:10]):
                    phase = rec.get("phase")
                    if phase in ("up", "recovery", "expansion"):
                        features["cycle_up"] = 1
                    elif phase in ("down", "downturn", "contraction"):
                        features["cycle_down"] = 1
                if features["cycle_up"] or features["cycle_down"]:
                    return features
            for rec in self._rows(driver, """
                MATCH (s:Stock {code: $code})-[r:FOLLOWS_CYCLE]->(c:Cycle)
                RETURN c.phase AS phase
            """, code=stock_code):
                phase = rec.get("phase")
                if phase in ("up", "recovery", "expansion"):
                    features["cycle_up"] = 1
                elif phase in ("down", "downturn", "contraction"):
                    features["cycle_down"] = 1
        except Exception as e:
            logger.debug("cycle features failed for %s: %s", stock_code, e)
        return features
