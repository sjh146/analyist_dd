"""
init_stocks.py — KOSPI/KOSDAQ 종목 초기화 + pgvector 임베딩 + Neo4j 동기화

Usage (fresh Docker setup 이후):
  # 1. PostgreSQL stocks 테이블 + pgvector 임베딩 + Neo4j 동기화 (한번에)
  docker exec -it stock_news_analyzer python /app/scripts/init_stocks.py --all

  # 개별 단계 실행:
  docker exec -it stock_news_analyzer python /app/scripts/init_stocks.py --scrape-only
  docker exec -it stock_news_analyzer python /app/scripts/init_stocks.py --vectorize-only
  docker exec -it stock_news_analyzer python /app/scripts/init_stocks.py --neo4j-only

의존성: httpx, psycopg2, neo4j, numpy (news-analyzer 컨테이너에 모두 설치됨)
"""

import argparse
import logging
import os
import re
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("init_stocks")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ─── PostgreSQL ───────────────────────────────────────────────────────

def pg_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", "stock_secure_password_2026"),
    )


# ─── 네이버 스크래핑 ───────────────────────────────────────────────────

def scrape_naver_market(market_name: str, sosok: str):
    """네이버 금융 시가총액순위에서 특정 시장 전체 종목 수집"""
    import httpx

    stocks = []
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30) as c:
        # 전체 페이지 수 확인
        r = c.get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page=1")
        pages = re.findall(r"page=(\d+)", r.text)
        if not pages:
            log.warning(f"{market_name}: 페이지 수를 찾을 수 없음")
            return stocks
        max_page = max(int(p) for p in pages)
        log.info(f"{market_name}: {max_page}페이지 스크래핑 시작")

        for page in range(1, max_page + 1):
            r = c.get(
                f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
            )
            codes = re.findall(r'/item/main\.naver\?code=(\d+)', r.text)
            names = re.findall(
                r'<a href="/item/main.naver\?code=\d+"[^>]*>([^<]+)</a>', r.text
            )
            for i, code in enumerate(codes):
                stocks.append({
                    "code": code,
                    "name": names[i] if i < len(names) else "",
                    "market": market_name,
                })
            if page % 10 == 0 or page == max_page:
                log.info(f"  {market_name} {page}/{max_page} ({len(stocks)}개)")
            time.sleep(0.1)

    return stocks


def scrape_all_stocks() -> list:
    """코스피 + 코스닥 전체 종목 반환"""
    kospi = scrape_naver_market("KOSPI", "0")
    kosdaq = scrape_naver_market("KOSDAQ", "1")
    all_stocks = kospi + kosdaq
    log.info(f"스크래핑 완료: KOSPI {len(kospi)} + KOSDAQ {len(kosdaq)} = {len(all_stocks)}")
    return all_stocks


# ─── PostgreSQL 저장 ──────────────────────────────────────────────────

def save_stocks_to_pg(stocks: list) -> int:
    """stocks 테이블에 UPSERT"""
    conn = pg_connect()
    conn.autocommit = True
    cur = conn.cursor()

    # stocks 테이블이 없으면 생성
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            id SERIAL PRIMARY KEY,
            stock_code VARCHAR(10) NOT NULL UNIQUE,
            stock_name VARCHAR(100) NOT NULL,
            market VARCHAR(10) NOT NULL,
            sector VARCHAR(100),
            industry VARCHAR(100),
            market_cap BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    count = 0
    for s in stocks:
        try:
            cur.execute(
                """
                INSERT INTO stocks (stock_code, stock_name, market)
                VALUES (%s, %s, %s)
                ON CONFLICT (stock_code)
                DO UPDATE SET stock_name = EXCLUDED.stock_name,
                              market = EXCLUDED.market,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (s["code"], s["name"], s["market"]),
            )
            count += 1
        except Exception as e:
            log.warning(f"  저장 실패 {s['code']}: {e}")

    cur.execute("SELECT count(*) FROM stocks")
    total = cur.fetchone()[0]
    cur.close()
    conn.close()
    log.info(f"PostgreSQL 저장 완료: {count}개 upsert, 총 {total}개")
    return total


# ─── pgvector 임베딩 생성 ─────────────────────────────────────────────

def vectorize_stocks():
    """모든 종목에 대해 combined(1024-d) 임베딩 생성"""
    import numpy as np

    conn = pg_connect()
    conn.autocommit = True
    cur = conn.cursor()

    # 종목 목록
    cur.execute("SELECT stock_code, stock_name, sector, market FROM stocks ORDER BY stock_code")
    stocks = cur.fetchall()

    # vectorizer 간소화: sector + market 기반 임베딩
    VECTOR_DIM = 1024
    sector_list = sorted(set(
        s[2] for s in stocks if s[2]
    ))

    sector_index = {s: i for i, s in enumerate(sector_list)}
    market_index = {"KOSPI": 0, "KOSDAQ": 1}

    saved = 0
    for code, name, sector, market in stocks:
        vec = np.zeros(VECTOR_DIM, dtype=np.float32)

        # sector one-hot
        if sector and sector in sector_index:
            idx = sector_index[sector]
            if idx < VECTOR_DIM:
                vec[idx] = 1.0

        # market indicator
        mi = market_index.get(market, 0)
        if mi < VECTOR_DIM:
            vec[mi] = 0.5

        # stock_code 해시 기반 시드로 랜덤 요소 추가 (종목 간 완전 동일 방지)
        seed = int(code) % 10000 if code.isdigit() else hash(code) % 10000
        rng = np.random.RandomState(seed)
        noise = rng.uniform(-0.05, 0.05, VECTOR_DIM).astype(np.float32)
        vec += noise

        # 정규화 (unit length)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        # 저장
        vec_str = "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
        try:
            cur.execute(
                """
                INSERT INTO stock_vectors (stock_code, vector_type, embedding, metadata)
                VALUES (%s, 'combined', %s::vector, '{}'::jsonb)
                ON CONFLICT (stock_code, vector_type)
                DO UPDATE SET embedding = EXCLUDED.embedding,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (code, vec_str),
            )
            saved += 1
        except Exception as e:
            log.warning(f"  벡터 저장 실패 {code}: {e}")

        if saved % 500 == 0:
            log.info(f"  벡터화 {saved}/{len(stocks)}")

    cur.close()
    conn.close()

    # 통계 확인
    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM stock_vectors WHERE vector_type = 'combined'")
    total = cur.fetchone()[0]
    cur.close()
    conn.close()
    log.info(f"벡터화 완료: {saved}개 저장, 총 {total}개")
    return total


# ─── Neo4j 동기화 ─────────────────────────────────────────────────────

def sync_neo4j():
    """Neo4j에 Stock 노드 동기화 + BELONGS_TO 관계"""
    from neo4j import GraphDatabase

    neo4j_driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
        auth=(
            os.environ.get("NEO4J_USER", "neo4j"),
            os.environ.get("NEO4J_PASSWORD", "neo4j_secure_password_2026"),
        ),
    )

    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("SELECT stock_code, stock_name, market, sector FROM stocks ORDER BY stock_code")
    stocks = cur.fetchall()
    cur.close()
    conn.close()

    with neo4j_driver.session() as session:
        # garbage 정리
        codes = [s[0] for s in stocks]
        result = session.run(
            "MATCH (s:Stock) WHERE NOT s.code IN $codes DETACH DELETE s RETURN count(*) as deleted",
            codes=codes,
        )
        deleted = result.single()["deleted"]
        log.info(f"Neo4j garbage 노드 {deleted}개 삭제")

        # Stock 노드 MERGE + BELONGS_TO
        sectors = set()
        for code, name, market, sector in stocks:
            session.run(
                "MERGE (s:Stock {code: $code}) SET s.name = $name, s.market = $market",
                code=code, name=name, market=market,
            )
            if sector:
                sectors.add(sector)

        # Sector 노드
        for sector in sorted(sectors):
            session.run("MERGE (sec:Sector {name: $name})", name=sector)

        # BELONGS_TO
        rel_count = 0
        for code, name, market, sector in stocks:
            if sector:
                try:
                    session.run(
                        """
                        MATCH (s:Stock {code: $code})
                        MATCH (sec:Sector {name: $sector})
                        MERGE (s)-[:BELONGS_TO]->(sec)
                        """,
                        code=code, sector=sector,
                    )
                    rel_count += 1
                except Exception:
                    pass

        result = session.run("MATCH (s:Stock) RETURN count(s) as cnt")
        stock_cnt = result.single()["cnt"]
        result = session.run("MATCH (sec:Sector) RETURN count(sec) as cnt")
        sector_cnt = result.single()["cnt"]

    neo4j_driver.close()
    log.info(f"Neo4j 동기화 완료: Stock {stock_cnt}개, Sector {sector_cnt}개, BELONGS_TO {rel_count}개")
    return stock_cnt


# ─── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="KOSPI/KOSDAQ 종목 초기화 + pgvector 임베딩 + Neo4j 동기화",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--all", action="store_true", help="전체 실행 (스크래핑 → PG → 벡터화 → Neo4j)")
    parser.add_argument("--scrape-only", action="store_true", help="네이버 스크래핑 + PG 저장만")
    parser.add_argument("--vectorize-only", action="store_true", help="pgvector 임베딩 생성만")
    parser.add_argument("--neo4j-only", action="store_true", help="Neo4j 동기화만")
    parser.add_argument("--skip-vectorize", action="store_true", help="벡터화 생략 (스크래핑 + Neo4j만)")

    args = parser.parse_args()

    # 기본: --all
    do_scrape = args.all or args.scrape_only or (not args.vectorize_only and not args.neo4j_only)
    do_vectorize = args.all or args.vectorize_only or (not args.scrape_only and not args.neo4j_only and not args.skip_vectorize)
    do_neo4j = args.all or args.neo4j_only or (not args.scrape_only and not args.vectorize_only)

    elapsed_total = time.time()

    if do_scrape:
        t0 = time.time()
        log.info("=" * 50)
        log.info("Step 1/3: 네이버 스크래핑 → PostgreSQL 저장")
        log.info("=" * 50)
        stocks = scrape_all_stocks()
        if stocks:
            save_stocks_to_pg(stocks)
        log.info(f"  ⏱ {time.time() - t0:.1f}초\n")

    if do_vectorize:
        t0 = time.time()
        log.info("=" * 50)
        log.info("Step 2/3: pgvector 임베딩 생성")
        log.info("=" * 50)
        vectorize_stocks()
        log.info(f"  ⏱ {time.time() - t0:.1f}초\n")

    if do_neo4j:
        t0 = time.time()
        log.info("=" * 50)
        log.info("Step 3/3: Neo4j 동기화")
        log.info("=" * 50)
        sync_neo4j()
        log.info(f"  ⏱ {time.time() - t0:.1f}초\n")

    log.info(f"✅ 전체 완료 (총 {time.time() - elapsed_total:.1f}초)")


if __name__ == "__main__":
    main()
