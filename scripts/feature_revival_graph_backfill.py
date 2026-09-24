"""테마·트윈·섹터 그래프 백필 (2026-09-24).

WHY: GraphFeatures 리더가 읽는 관계가 Neo4j 에 아예 없어서(실측: TWIN_OF 0,
BELONGS_TO 0, Cycle 0) 테마/트윈/섹터 피처가 전부 0 이었다. 또 뉴스 그래프 라이터는
``HAS_THEME`` 를 쓰는데 리더는 ``PART_OF_THEME`` 를 읽는 스키마 불일치가 있었다.

이 스크립트는 **추가만** 한다(기존 노드/관계 삭제 없음, Neo4j 재시작 없음):
  --themes  news_event_extraction.themes(JSONB)에서 (종목,테마)별 언급수·비중·일별이력을
            계산해 HAS_THEME(및 스키마 호환용 PART_OF_THEME) 의 relevance/count/
            last_seen/history 를 채운다. 그래프에만 있고 추출표에 없는 쌍은 건드리지 않는다.
  --twins   market_data 120거래일 수익률 상관계수로 TWIN_OF(correlation) 를 만든다
            (종목당 상위 5개, |corr|>=0.6, 쌍마다 1회만 기록 — 리더는 무방향 조회).
  --sectors stocks.sector 가 채워진 경우 Sector 노드 + BELONGS_TO 를 MERGE 한다.
  --report  현재 그래프 카운트만 출력.

실행(컨테이너):
    docker exec stock_xgboost_ml python /app/scripts/feature_revival_graph_backfill.py --themes --twins
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
from neo4j import GraphDatabase

HISTORY_DAYS = 120          # 일별 이력 보관 기간(테마 모멘텀용)
TWIN_WINDOW = 120           # 트윈 상관계수 계산 거래일
TWIN_TOP_K = 5
TWIN_MIN_CORR = 0.6
MARKET_DATA_VALID = "NOT (open_price = 0 AND high_price = 0 AND low_price = 0)"


def pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def neo4j_driver():
    return GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
    )


# --------------------------------------------------------------------------- themes
def backfill_themes(pg, driver) -> int:
    """(종목,테마) 언급수·비중·일별이력을 HAS_THEME / PART_OF_THEME 에 쓴다."""
    cur = pg.cursor()
    cur.execute("""
        SELECT stock_code, created_at::date AS d, themes
        FROM news_event_extraction
        WHERE themes IS NOT NULL AND jsonb_typeof(themes) = 'array'
    """)
    rows = cur.fetchall()
    cur.close()

    cutoff = date.today() - timedelta(days=HISTORY_DAYS)
    per_stock_total = defaultdict(int)
    per_pair_count = defaultdict(int)
    per_pair_last = {}
    per_pair_hist = defaultdict(lambda: defaultdict(int))
    for stock_code, d, themes in rows:
        try:
            names = json.loads(themes) if isinstance(themes, str) else themes
        except Exception:
            continue
        if not isinstance(names, list):
            continue
        for name in names:
            if not isinstance(name, str) or not name:
                continue
            per_stock_total[stock_code] += 1
            per_pair_count[(stock_code, name)] += 1
            if per_pair_last.get((stock_code, name)) is None or d > per_pair_last[(stock_code, name)]:
                per_pair_last[(stock_code, name)] = d
            if d >= cutoff:
                per_pair_hist[(stock_code, name)][d.isoformat()] += 1

    payload = []
    for (stock_code, name), n in per_pair_count.items():
        total = per_stock_total[stock_code] or 1
        hist = per_pair_hist.get((stock_code, name), {})
        payload.append({
            "stock_code": stock_code,
            "theme": name,
            "relevance": round(n / total, 6),
            "count": int(n),
            "last_seen": per_pair_last[(stock_code, name)].isoformat(),
            "history": json.dumps(dict(sorted(hist.items())), ensure_ascii=False),
        })

    written = 0
    with driver.session() as s:
        for i in range(0, len(payload), 200):
            batch = payload[i:i + 200]
            s.run("""
                UNWIND $rows AS row
                MERGE (t:Theme {name: row.theme})
                MERGE (sk:Stock {code: row.stock_code})
                MERGE (sk)-[r:HAS_THEME]->(t)
                SET r.relevance = row.relevance,
                    r.count = row.count,
                    r.last_seen = row.last_seen,
                    r.history = row.history,
                    r.source = 'news_event_extraction'
                MERGE (sk)-[r2:PART_OF_THEME]->(t)
                SET r2.relevance = row.relevance,
                    r2.count = row.count,
                    r2.last_seen = row.last_seen,
                    r2.history = row.history,
                    r2.source = 'news_event_extraction'
            """, rows=batch)
            written += len(batch)
    print(f"[themes] {written} (stock,theme) 쌍에 relevance/count/history 기록 "
          f"(종목 {len(per_stock_total)}개)")
    return written


# ---------------------------------------------------------------------------- twins
def backfill_twins(pg, driver) -> int:
    cur = pg.cursor()
    cur.execute(f"""
        SELECT md.stock_code, md.trade_date, md.close_price
        FROM market_data md
        JOIN stocks s ON s.stock_code = md.stock_code
        WHERE s.instrument_type = 'STOCK'
          AND md.trade_date >= (SELECT max(trade_date) FROM market_data) - INTERVAL '200 days'
          AND {MARKET_DATA_VALID}
    """)
    rows = cur.fetchall()
    cur.close()
    if not rows:
        print("[twins] market_data 없음 — skip")
        return 0

    df = pd.DataFrame(rows, columns=["stock_code", "trade_date", "close_price"])
    px = df.pivot(index="trade_date", columns="stock_code", values="close_price")
    px = px.sort_index().astype(float)
    rets = px.pct_change(fill_method=None).iloc[-TWIN_WINDOW:]
    rets = rets.loc[:, rets.notna().sum() >= max(30, TWIN_WINDOW // 2)]

    codes = list(rets.columns)
    m = rets.to_numpy(dtype=np.float64)
    m = np.where(np.isfinite(m), m, np.nan)
    # 결측은 열 평균으로 대체 후 상관계수 계산(상관은 스케일 불변)
    col_mean = np.nanmean(m, axis=0)
    inds = np.where(np.isnan(m))
    m[inds] = np.take(col_mean, inds[1])
    corr = np.corrcoef(m, rowvar=False)
    corr = np.nan_to_num(corr, nan=0.0)

    pairs = {}
    for i, code in enumerate(codes):
        order = np.argsort(-corr[i])
        picked = 0
        for j in order:
            if j == i:
                continue
            c = float(corr[i][j])
            if c < TWIN_MIN_CORR:
                break
            a, b = sorted((code, codes[j]))
            pairs[(a, b)] = round(c, 6)
            picked += 1
            if picked >= TWIN_TOP_K:
                break

    payload = [{"a": a, "b": b, "corr": c} for (a, b), c in pairs.items()]
    with driver.session() as s:
        for i in range(0, len(payload), 500):
            s.run("""
                UNWIND $rows AS row
                MERGE (a:Stock {code: row.a})
                MERGE (b:Stock {code: row.b})
                MERGE (a)-[r:TWIN_OF]->(b)
                SET r.correlation = row.corr, r.window_days = $win
            """, rows=payload[i:i + 500], win=TWIN_WINDOW)
    print(f"[twins] 종목 {len(codes)}개 → TWIN_OF {len(payload)}개 "
          f"(window={TWIN_WINDOW}d, min_corr={TWIN_MIN_CORR})")
    return len(payload)


# -------------------------------------------------------------------------- sectors
def backfill_sectors(pg, driver) -> int:
    cur = pg.cursor()
    cur.execute("SELECT stock_code, stock_name, market, sector FROM stocks WHERE sector IS NOT NULL AND sector <> ''")
    rows = cur.fetchall()
    cur.close()
    if not rows:
        print("[sectors] stocks.sector 가 전부 비어 있음 — skip")
        return 0
    payload = [{"code": c, "name": n, "market": mk, "sector": sec} for c, n, mk, sec in rows]
    with driver.session() as s:
        for i in range(0, len(payload), 500):
            s.run("""
                UNWIND $rows AS row
                MERGE (sec:Sector {name: row.sector})
                MERGE (sk:Stock {code: row.code})
                MERGE (sk)-[:BELONGS_TO]->(sec)
            """, rows=payload[i:i + 500])
    print(f"[sectors] {len(payload)}개 종목 BELONGS_TO 기록")
    return len(payload)


def backfill_cycles(pg, driver) -> int:
    """일별 시장 사이클 국면(Cycle {date, phase})을 기록한다.

    지수 시계열이 DB에 없으므로 market_data 전 종목(STOCK)의 동일가중 지수
    (평균 종가)와 그 120일 이동평균을 비교해 up/down 을 판정한다.
    리더(GraphFeatures)는 date 인자를 받았을 때만 이 노드를 읽는다
    (feature_pipeline 이 date 를 넘기도록 하는 1줄 패치가 필요 — 보고서 참고).
    """
    cur = pg.cursor()
    cur.execute(f"""
        SELECT trade_date::text, AVG(close_price)
        FROM market_data md
        JOIN stocks s ON s.stock_code = md.stock_code
        WHERE s.instrument_type = 'STOCK' AND {MARKET_DATA_VALID}
        GROUP BY 1 ORDER BY 1
    """)
    rows = cur.fetchall()
    cur.close()
    if not rows:
        print("[cycles] market_data 없음 — skip")
        return 0
    df = pd.DataFrame(rows, columns=["d", "idx"]).set_index("d")["idx"].astype(float)
    ma = df.rolling(120, min_periods=20).mean()
    payload = []
    for d, v in df.items():
        m = ma[d]
        if pd.isna(m):
            continue
        payload.append({"date": d, "phase": "up" if v >= m else "down"})
    with driver.session() as s:
        for i in range(0, len(payload), 500):
            s.run("""
                UNWIND $rows AS row
                MERGE (c:Cycle {date: row.date})
                SET c.phase = row.phase,
                    c.source = 'market_data_ew_index'
            """, rows=payload[i:i + 500])
    print(f"[cycles] Cycle 노드 {len(payload)}개 기록")
    return len(payload)


def report(driver):
    with driver.session() as s:
        for q in ("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY c DESC",
                  "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"):
            print("---", q)
            print(s.run(q).data())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--themes", action="store_true")
    ap.add_argument("--twins", action="store_true")
    ap.add_argument("--sectors", action="store_true")
    ap.add_argument("--cycles", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if not any([args.themes, args.twins, args.sectors, args.cycles, args.all, args.report]):
        ap.error("nothing to do (--themes/--twins/--sectors/--cycles/--all/--report)")

    pg = pg_connect()
    driver = neo4j_driver()
    try:
        if args.report:
            report(driver)
        if args.themes or args.all:
            backfill_themes(pg, driver)
        if args.twins or args.all:
            backfill_twins(pg, driver)
        if args.sectors or args.all:
            backfill_sectors(pg, driver)
        if args.cycles or args.all:
            backfill_cycles(pg, driver)
    finally:
        pg.close()
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
