#!/usr/bin/env python3
"""[자체점검] FeatureStore(FS1) — 실제 스키마 정합 + '완전성 증명 없으면 제공 안 함' 계약.

왜(실측 2026-10-01): 이 스토어는 **한 번도 동작한 적이 없다**(feature_values 0행). 원인 3개
  ① 컬럼명: 코드는 `feature_value` 를 읽고 썼는데 실제 컬럼은 `value`(numeric(15,6))
  ② FK: feature_name → feature_definitions(feature_name) 인데 feature_definitions 0행
  ③ 부분 커버리지가 그대로 반환(요청 일부만 있어도 '적중'으로 오인 → 학습 표본 조용히 축소)

이 파일은 **DB 에 쓰지 않는다**(초기 대량 적재는 승인 대상). 스키마 조회는 읽기 전용이다.
실행: `docker exec stock_xgboost_ml python /app/scripts/_feature_store_test.py`
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/app")

from app.feature_engine.feature_store import (  # noqa: E402
    FeatureStore, VALUES_TABLE, VALUES_COLUMNS, WATERMARK_SOURCES)

PASS = FAIL = NOTE = 0


def check(name, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      got ={got!r}\n      want={want!r}")


def note(msg):
    global NOTE
    NOTE += 1
    print(f"NOTE  {msg}")


class FakeCursor:
    """executed SQL/params 를 기록하고, 요청 종류에 따라 정해진 행을 돌려준다."""

    def __init__(self, conn):
        self.conn = conn
        self._result = []

    def execute(self, sql, params=None):
        self.conn.calls.append((sql, params))
        self.conn.last_sql = sql
        s = " ".join(sql.split())
        if s.upper().startswith("SELECT MAX("):
            col = s[len("SELECT max("):].split(")")[0]
            self._result = [(self.conn.watermarks.get(col, "2026-09-30"),)]
        elif "FROM feature_store.feature_values" in s and s.upper().startswith("SELECT STOCK_CODE"):
            self._result = list(self.conn.rows)
        elif "FROM feature_store.feature_values" in s:
            self._result = [("atr", 1.5), ("volatility_20d", 0.2)]
        else:
            self._result = []

    def executemany(self, sql, rows):
        self.conn.calls.append((sql, list(rows)))

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows=(), watermarks=None):
        self.rows = list(rows)
        self.watermarks = watermarks or {}
        self.calls = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


FULL_ROWS = [
    ("005930", "2026-09-01", "atr", 1.0),
    ("005930", "2026-09-01", "rsi", 55.0),
    ("005930", "2026-09-02", "atr", 1.1),
    ("005930", "2026-09-02", "rsi", 56.0),
]
WANT = {("005930", "2026-09-01"), ("005930", "2026-09-02")}

print("== 1) 실행되는 SQL 이 실제 컬럼명(`value`)만 쓰는가 ==")
import re  # noqa: E402

src = open("/app/app/feature_engine/feature_store.py", encoding="utf-8").read()
code_only = re.sub(r'"""[\s\S]*?"""', "", src)      # 사고 기록 docstring 은 제외
check("코드에 옛 컬럼명 잔존 없음", bool(re.search(r"feature_value(?!s)", code_only)), False)
check("테이블명은 여전히 feature_values", "feature_store.feature_values" in code_only, True)

print("== 2) 실제 DB 스키마와 모듈 가정의 일치(읽기 전용) ==")
live = None
try:
    import psycopg2  # noqa
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        dbname=os.environ.get("POSTGRES_DB", "stock"),
        connect_timeout=5,
    )
    live = FeatureStore.schema_report(conn)
    problems = FeatureStore.schema_mismatch(live)
    check("스키마 불일치 없음", problems, [])
    check("feature_values 실제 컬럼", sorted(live[VALUES_TABLE]),
          sorted(["stock_code", "feature_name", "date", "value", "created_at"]))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM feature_store.feature_values")
    n_rows = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM feature_store.feature_definitions")
    n_defs = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"      실측: feature_values={n_rows}행 · feature_definitions={n_defs}행 (초기 적재 전)")
except Exception as e:  # DB 미도달은 코드 회귀가 아니다
    note(f"DB 미도달 — 스키마 실측 건너뜀({type(e).__name__}: {e})")

print("== 3) strict + 전량 커버 → 제공 ==")
st = FeatureStore(pg_conn=FakeConn(FULL_ROWS), strict=True)
df = st.load_batch(["005930"], "2026-09-01", "2026-09-02", expected_keys=WANT)
check("행수", len(df), 2)
check("date 는 ISO 문자열", sorted(df["date"].tolist()), ["2026-09-01", "2026-09-02"])
check("피처 컬럼", "atr" in df.columns and "rsi" in df.columns, True)
check("거부사유 없음", st.last_refusal, None)

print("== 4) strict + 1키 결손 → 제공 거부(폴백) ==")
st = FeatureStore(pg_conn=FakeConn(FULL_ROWS), strict=True)
df = st.load_batch(["005930"], "2026-09-01", "2026-09-02",
                   expected_keys=WANT | {("005930", "2026-09-03")})
check("빈 DataFrame", df.empty, True)
check("사유에 '부분 커버리지'", "부분 커버리지" in (st.last_refusal or ""), True)

print("== 5) strict + expected_keys 미지정 → 제공 거부(증명 없음) ==")
st = FeatureStore(pg_conn=FakeConn(FULL_ROWS), strict=True)
df = st.load_batch(["005930"], "2026-09-01", "2026-09-02")
check("빈 DataFrame", df.empty, True)
check("사유에 '완전성 증명 없음'", "완전성 증명" in (st.last_refusal or ""), True)

print("== 6) allow_partial=True → 옛 동작(명시적 opt-in) ==")
st = FeatureStore(pg_conn=FakeConn(FULL_ROWS), strict=True)
df = st.load_batch(["005930"], "2026-09-01", "2026-09-02",
                   expected_keys=WANT | {("005930", "2026-09-03")}, allow_partial=True)
check("부분 반환 행수", len(df), 2)

print("== 7) 매니페스트(코드 서명·원천 워터마크) 무효화 ==")
tmp = tempfile.mkdtemp()
man = os.path.join(tmp, "store.manifest.json")
conn = FakeConn(FULL_ROWS, watermarks={"trade_date": "2026-09-30"})
st = FeatureStore(pg_conn=conn, manifest_path=man)
df = st.load_batch(["005930"], "2026-09-01", "2026-09-02", expected_keys=WANT)
check("매니페스트 없음 → 거부", df.empty, True)
check("사유에 '매니페스트 없음'", "매니페스트 없음" in (st.last_refusal or ""), True)
st.write_manifest(["005930"], "2026-09-01", "2026-09-02", n_keys=2)
check("매니페스트 파일 생성", os.path.exists(man), True)
df = st.load_batch(["005930"], "2026-09-01", "2026-09-02", expected_keys=WANT)
check("서명·워터마크 일치 → 제공", len(df), 2)
with open(man, encoding="utf-8") as f:
    payload = json.load(f)
payload["code_sig"] = -1.0
with open(man, "w", encoding="utf-8") as f:
    json.dump(payload, f)
df = st.load_batch(["005930"], "2026-09-01", "2026-09-02", expected_keys=WANT)
check("코드 서명 불일치 → 거부", df.empty, True)
check("사유에 '코드 서명'", "코드 서명" in (st.last_refusal or ""), True)
st.write_manifest(["005930"], "2026-09-01", "2026-09-02", n_keys=2)
conn.watermarks["trade_date"] = "2026-10-01"   # 백필 발생
df = st.load_batch(["005930"], "2026-09-01", "2026-09-02", expected_keys=WANT)
check("원천 워터마크 변경 → 거부", df.empty, True)
check("사유에 '워터마크'", "워터마크" in (st.last_refusal or ""), True)

print("== 8) save_features: 정의 등록(FK) → `value` 로 upsert ==")
conn = FakeConn()
st = FeatureStore(pg_conn=conn)
ok = st.save_features("005930", "2026-09-01", {"atr": 1.0, "rsi": 55.0, "note": "x"})
check("저장 성공", ok, True)
sqls = [" ".join(c[0].split()) for c in conn.calls]
check("feature_definitions INSERT 가 먼저", "INSERT INTO feature_store.feature_definitions" in sqls[0], True)
check("feature_values INSERT 에 `value`", "feature_name, date, value" in sqls[1], True)
check("잘못된 컬럼명 없음", any(re.search(r"feature_value(?!s)", s) for s in sqls), False)
rows = conn.calls[1][1]
check("숫자만 저장(문자열 제외)", sorted(r[1] for r in rows), ["atr", "rsi"])
check("pg_conn=None → False", FeatureStore(pg_conn=None).save_features("005930", "2026-09-01", {"a": 1.0}), False)

print("== 9) load_features: `value` 컬럼 사용 ==")
conn = FakeConn()
st = FeatureStore(pg_conn=conn)
got = st.load_features("005930", "2026-09-01")
check("값 파싱", got, {"atr": 1.5, "volatility_20d": 0.2})
check("SQL 에 `value`", "SELECT feature_name, value" in " ".join(conn.calls[0][0].split()), True)

print("== 10) 파이프라인 통합: 스토어에 expected_keys 를 넘기는가 ==")
try:
    from app.feature_engine.feature_pipeline import FeaturePipeline

    class StubStore(FeatureStore):
        def __init__(self):
            super().__init__(pg_conn=None, strict=True)
            self.seen = None

        def load_batch(self, stock_codes, start_date, end_date, expected_keys=None, allow_partial=False):
            self.seen = expected_keys
            import pandas as pd
            return pd.DataFrame()

    stub = StubStore()
    pipe = FeaturePipeline(pg_conn=None, use_feature_store=True, feature_store=stub)
    out = pipe.build_training_features(["005930"], "2026-09-01", "2026-09-02")
    check("load_batch 호출됨", stub.seen is not None, True)
    check("expected_keys 로 전달(빈 유니버스 = 빈 집합)", stub.seen, set())
    check("폴백 결과는 빈 DF", out.empty, True)
    check("기본값(use_feature_store=False)은 스토어 없음",
          FeaturePipeline(pg_conn=None).feature_store, None)
except Exception as e:
    check(f"파이프라인 통합 예외({type(e).__name__}: {e})", "ok", "exception")

print(f"\n{'=' * 60}\nPASS {PASS} · FAIL {FAIL} · NOTE {NOTE}")
sys.exit(1 if FAIL else 0)
