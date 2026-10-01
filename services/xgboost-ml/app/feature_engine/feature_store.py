"""
Feature Store — feature_store.feature_values (PostgreSQL) 영속화/조회.

⚠ 실측(2026-10-01, FS1): 이 모듈은 **한 번도 실제로 동작한 적이 없다**(feature_values 0행).
  3중 결함이 있었다:
   ① 컬럼명 불일치 — 코드는 `feature_value` 를 읽고 쓰는데 실제 테이블 컬럼은 `value`
      (numeric(15,6)) 이다 → 모든 SQL 이 UndefinedColumn 으로 죽고 except 가 삼켰다.
   ② FK — `feature_values.feature_name → feature_definitions(feature_name)` 인데
      feature_definitions 가 **0행**이라 어떤 INSERT 도 FK 위반으로 실패한다.
   ③ 부분 커버리지를 완전한 패널로 오인 — 요청한 (stock,date) 중 일부만 있어도 그대로 반환했다
      (FS1 요구 ①: 이게 켜지면 학습 표본이 조용히 줄어든다).

재작성 계약(보수적 — '증명 없으면 제공하지 않는다'):
  · `strict=True`(기본) 에서는 **완전성 증명이 없으면 빈 DataFrame 을 돌려준다** →
    호출자(FeaturePipeline.build_training_features)는 빈 값을 '스토어 미스'로 보고 재계산한다.
    완전성 증명 = ① `expected_keys` 전량 커버 ② (주면) 코드 서명·원천 워터마크 일치.
  · `allow_partial=True` 를 **명시**해야 옛 동작(부분 반환)이 나온다.
  · 스키마 자기검증 `schema_report(pg_conn)` — 실제 컬럼명이 이 모듈의 가정과 다르면 그 자리에서 드러난다.

검증: `docker exec stock_xgboost_ml python /app/scripts/_feature_store_test.py`
(pytest 없음 — PASS/FAIL 자체점검. DB 쓰기는 하지 않는다: 초기 대량 적재는 승인 대상.)
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

VALUES_TABLE = "feature_store.feature_values"
DEFS_TABLE = "feature_store.feature_definitions"

# 실제 테이블 컬럼(2026-10-01 실측). 이 목록이 진실의 원천이다.
VALUES_COLUMNS = {
    "stock_code": "character varying",
    "feature_name": "character varying",
    "date": "date",
    "value": "numeric(15,6)",
    "created_at": "timestamp without time zone",
}
DEFS_COLUMNS = {
    "feature_name": "character varying",
    "category": "character varying",
    "description": "text",
    "version": "integer",
    "created_at": "timestamp without time zone",
}

# 원천 워터마크 후보 — 백필(R10·R17)이 들어오면 옛 값이 학습에 섞이는 것을 막는다.
# (table, date_column) 만 본다: 비용이 max(index) 수준이고 결측/스키마 차이는 조용히 건너뛴다.
WATERMARK_SOURCES = [
    ("market_data", "trade_date"),
    ("event_features", "event_date"),
    ("supply_market_features", "trade_date"),
    ("financial_statements", "rcept_dt"),
]


class FeatureStore:
    """Persist and load feature values to/from feature_store.feature_values."""

    def __init__(self, pg_conn=None, strict: bool = True,
                 manifest_path: Optional[str] = None):
        """pg_conn: psycopg2 connection or None for graceful degradation.

        strict        : 완전성 증명이 없으면 빈 DataFrame(재계산 폴백) — 기본값.
        manifest_path : 코드 서명·원천 워터마크를 적어 두는 로컬 사이드카 JSON.
                        DDL 없이 무효화 키를 갖기 위한 장치다(운영 DB 스키마 변경 금지).
        """
        self.pg_conn = pg_conn
        self.strict = strict
        self.manifest_path = manifest_path
        self.last_refusal: Optional[str] = None

    # ────────────────────────── 무효화 키 ──────────────────────────
    @staticmethod
    def code_sig() -> Optional[float]:
        """feature_engine 패키지 .py 최신 mtime — 피처 코드가 바뀌면 캐시 무효."""
        try:
            import app.feature_engine as pkg
            d = os.path.dirname(os.path.abspath(pkg.__file__))
            return round(max(os.path.getmtime(os.path.join(d, f))
                             for f in os.listdir(d) if f.endswith(".py")), 3)
        except Exception:
            try:
                d = os.path.dirname(os.path.abspath(__file__))
                return round(max(os.path.getmtime(os.path.join(d, f))
                                 for f in os.listdir(d) if f.endswith(".py")), 3)
            except Exception:
                return None

    @staticmethod
    def source_watermark(pg_conn) -> Dict[str, str]:
        """원천별 최신 날짜(백필 감지용). 실패한 원천은 건너뛴다(비어 있으면 '모름')."""
        wm: Dict[str, str] = {}
        if pg_conn is None:
            return wm
        for table, col in WATERMARK_SOURCES:
            cur = None
            try:
                cur = pg_conn.cursor()
                cur.execute(f"SELECT max({col})::text FROM {table}")
                row = cur.fetchone()
                if row and row[0]:
                    wm[table] = str(row[0])
            except Exception:
                try:
                    if hasattr(pg_conn, "rollback"):
                        pg_conn.rollback()
                except Exception:
                    pass
            finally:
                try:
                    if cur is not None:
                        cur.close()
                except Exception:
                    pass
        return wm

    def write_manifest(self, stock_codes, start_date, end_date, n_keys: int,
                       extra: Optional[dict] = None) -> Optional[str]:
        """코드 서명·원천 워터마크를 사이드카 JSON 으로 남긴다(원자적 replace)."""
        if not self.manifest_path:
            return None
        payload = {
            "code_sig": self.code_sig(),
            "watermark": self.source_watermark(self.pg_conn),
            "stock_codes": [str(c) for c in stock_codes],
            "start_date": start_date,
            "end_date": end_date,
            "n_keys": int(n_keys),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        if extra:
            payload.update(extra)
        try:
            tmp = self.manifest_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self.manifest_path)
            return self.manifest_path
        except Exception as e:
            logger.warning(f"manifest write failed: {e}")
            return None

    def _manifest_reason(self) -> Optional[str]:
        """매니페스트가 요청과 어긋나면 거부 사유 문자열, 일치하면 None."""
        if not self.manifest_path:
            return None
        if not os.path.exists(self.manifest_path):
            return f"매니페스트 없음({self.manifest_path})"
        try:
            with open(self.manifest_path, encoding="utf-8") as f:
                man = json.load(f)
        except Exception as e:
            return f"매니페스트 읽기 실패({type(e).__name__})"
        cur_sig = self.code_sig()
        if man.get("code_sig") != cur_sig:
            return f"피처 코드 서명 불일치({man.get('code_sig')} → {cur_sig})"
        cur_wm = self.source_watermark(self.pg_conn)
        old_wm = man.get("watermark") or {}
        for k, v in cur_wm.items():
            if k in old_wm and old_wm[k] != v:
                return f"원천 워터마크 변경({k}: {old_wm[k]} → {v})"
        return None

    # ────────────────────────── 스키마 자기검증 ──────────────────────────
    @staticmethod
    def schema_report(pg_conn) -> Dict[str, list]:
        """실제 information_schema 컬럼 목록. 모듈 가정과 다르면 여기서 드러난다."""
        out: Dict[str, list] = {}
        if pg_conn is None:
            return out
        cur = pg_conn.cursor()
        try:
            for schema, table in (("feature_store", "feature_values"),
                                  ("feature_store", "feature_definitions")):
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                    (schema, table))
                out[f"{schema}.{table}"] = [r[0] for r in cur.fetchall()]
        finally:
            cur.close()
        return out

    @staticmethod
    def schema_mismatch(report: Dict[str, list]) -> list:
        """가정 컬럼과 실제 컬럼의 차이(missing = 가정에 있으나 실제 없음)."""
        problems = []
        for key, assumed in ((VALUES_TABLE, VALUES_COLUMNS), (DEFS_TABLE, DEFS_COLUMNS)):
            actual = report.get(key)
            if not actual:
                problems.append(f"{key}: 테이블/컬럼 조회 실패")
                continue
            missing = sorted(set(assumed) - set(actual))
            if missing:
                problems.append(f"{key}: 가정 컬럼 없음 {missing} (실제 {sorted(actual)})")
        return problems

    # ────────────────────────── 쓰기 ──────────────────────────
    def ensure_feature_definitions(self, names, category: str = "auto") -> bool:
        """feature_name 을 feature_definitions 에 등록(멱등). FK 때문에 선행 필수."""
        if self.pg_conn is None or not names:
            return False
        try:
            cur = self.pg_conn.cursor()
            cur.executemany(
                f"""
                INSERT INTO {DEFS_TABLE} (feature_name, category, version)
                VALUES (%s, %s, 1)
                ON CONFLICT (feature_name) DO NOTHING
                """,
                [(str(n), category) for n in names],
            )
            self.pg_conn.commit()
            cur.close()
            return True
        except Exception as e:
            logger.error(f"Failed to register feature definitions: {e}")
            return False

    def save_features(self, stock_code: str, date: str, features: dict) -> bool:
        """Upsert features to feature_store.feature_values."""
        if self.pg_conn is None:
            logger.warning("No pg_conn: cannot save features")
            return False
        try:
            rows = [(str(stock_code), str(name), str(date)[:10], float(value))
                    for name, value in features.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)]
            if not rows:
                return False
            # FK(feature_name → feature_definitions) 때문에 등록이 선행돼야 한다.
            if not self.ensure_feature_definitions([r[1] for r in rows]):
                logger.warning("feature definitions registration failed — 저장 건너뜀(FK)")
                return False
            cursor = self.pg_conn.cursor()
            cursor.executemany(
                f"""
                INSERT INTO {VALUES_TABLE} (stock_code, feature_name, date, value)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (stock_code, feature_name, date)
                DO UPDATE SET value = EXCLUDED.value
                """,
                rows,
            )
            self.pg_conn.commit()
            cursor.close()
            return True
        except Exception as e:
            logger.error(f"Failed to save features for {stock_code} {date}: {e}")
            return False

    # ────────────────────────── 읽기 ──────────────────────────
    def load_features(self, stock_code: str, date: str) -> dict:
        """Load features for a stock on a date. Returns {} on failure."""
        if self.pg_conn is None:
            logger.warning("No pg_conn: cannot load features")
            return {}
        try:
            cursor = self.pg_conn.cursor()
            cursor.execute(
                f"""
                SELECT feature_name, value
                FROM {VALUES_TABLE}
                WHERE stock_code = %s AND date = %s
                """,
                (stock_code, date),
            )
            result = {row[0]: float(row[1]) for row in cursor.fetchall() if row[1] is not None}
            cursor.close()
            return result
        except Exception as e:
            logger.error(f"Failed to load features for {stock_code} {date}: {e}")
            return {}

    @staticmethod
    def _norm_keys(stock_codes, start_date, end_date, expected_keys):
        if expected_keys is None:
            return None
        return {(str(s), str(d)[:10]) for s, d in expected_keys}

    def load_batch(self, stock_codes: list, start_date: str, end_date: str,
                   expected_keys=None, allow_partial: bool = False) -> pd.DataFrame:
        """Load features for multiple stocks.

        strict(기본): `expected_keys` 전량을 덮지 않으면 **빈 DataFrame**(=호출자가 재계산).
        allow_partial=True: 옛 동작(부분 반환) — 명시적 opt-in.
        """
        self.last_refusal = None
        empty = pd.DataFrame()
        if self.pg_conn is None:
            self.last_refusal = "pg_conn 없음"
            logger.warning("No pg_conn: cannot load batch features")
            return empty

        want = self._norm_keys(stock_codes, start_date, end_date, expected_keys)
        if self.strict and want is None and not allow_partial:
            self.last_refusal = ("완전성 증명 없음(expected_keys 미지정) — "
                                 "strict 모드에서는 제공하지 않는다")
            logger.warning(f"FeatureStore.load_batch 거부: {self.last_refusal}")
            return empty
        if self.strict and not allow_partial:
            reason = self._manifest_reason()
            if reason:
                self.last_refusal = reason
                logger.warning(f"FeatureStore.load_batch 거부: {reason}")
                return empty

        try:
            cursor = self.pg_conn.cursor()
            cursor.execute(
                f"""
                SELECT stock_code, date, feature_name, value
                FROM {VALUES_TABLE}
                WHERE stock_code = ANY(%s) AND date BETWEEN %s AND %s
                """,
                (list(stock_codes), start_date, end_date),
            )
            rows = cursor.fetchall()
            cursor.close()
            if not rows:
                self.last_refusal = "스토어 비어 있음"
                return empty
            df = pd.DataFrame(rows, columns=["stock_code", "date", "feature_name", "value"])
            df["stock_code"] = df["stock_code"].astype(str)
            df["date"] = df["date"].astype(str).str.slice(0, 10)
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            pivot = df.pivot_table(index=["stock_code", "date"], columns="feature_name",
                                   values="value").reset_index()
            pivot.columns.name = None

            if want is not None and not allow_partial:
                have = set(zip(pivot["stock_code"], pivot["date"]))
                missing = want - have
                if missing:
                    self.last_refusal = (f"부분 커버리지: 요청 {len(want)}키 중 "
                                         f"{len(missing)}키 결손(예 {sorted(missing)[:3]}) — 반환 거부")
                    logger.warning(f"FeatureStore.load_batch 거부: {self.last_refusal}")
                    return empty
                logger.info(f"FeatureStore 적중: {len(pivot)}행 / 요청 {len(want)}키 전량 커버")
            return pivot
        except Exception as e:
            self.last_refusal = f"조회 실패({type(e).__name__}: {e})"
            logger.error(f"Failed to load batch features: {e}")
            return empty

    def get_feature_names(self) -> list:
        """Return all registered feature names from the database."""
        if self.pg_conn is None:
            logger.warning("No pg_conn: cannot get feature names")
            return []
        try:
            cursor = self.pg_conn.cursor()
            cursor.execute(f"SELECT DISTINCT feature_name FROM {VALUES_TABLE} ORDER BY feature_name")
            names = [row[0] for row in cursor.fetchall()]
            cursor.close()
            return names
        except Exception as e:
            logger.error(f"Failed to get feature names: {e}")
            return []
