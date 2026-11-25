#!/usr/bin/env python3
"""테마 분류 테이블 생성·적재 + 테마별 일일 강도(theme_daily).

WHY(2026-11-24 사용자 지시 "db에 테마 분류 테이블도 추가"): 단타 후보를 '테마주 위주
변동성'으로 고르려면 테마 소속이 DB에 있어야 한다. 기존 `stocks.sector` 는 80종
GICS 계열 업종뿐이고 테마(2차전지·로봇·바이오 …) 정보가 없다.

산출 테이블
  themes(id, name, source)                     — 테마 목록
  stock_themes(stock_code, theme, confidence)  — 종목→테마(이름 키워드 1.0, 업종 매핑 0.6)
  theme_daily(theme, trade_date, members, avg_ret_pct, up_ratio)

사용:
  python3 scripts/build_theme_map.py
  python3 scripts/build_theme_map.py --days 60
"""
from __future__ import annotations

import argparse
import os
import re

import psycopg2

THEME_RULES = [
    ("2차전지", r"2차전지|배터리|에너지솔루션|리튬|양극재|음극재|분리막|전해액|ESS"),
    ("반도체", r"반도체|웨이퍼|파운드리|HBM|SEMI|전자재료"),
    ("로봇", r"로봇|로보|자동화|머신비전"),
    ("바이오", r"바이오|제약|헬스|메디|셀트리온|젠|팜|백신|진단"),
    ("AI", r"AI|인공지능|데이터|클라우드|딥러닝|솔루션"),
    ("게임", r"게임|엔터테인먼트|스튜디오|소프트맥스"),
    ("미디어", r"미디어|방송|콘텐츠|엔터|스튜디오"),
    ("화장품", r"화장품|뷰티|코스메틱|더마"),
    ("방산", r"방산|항공우주|디펜스|국방"),
    ("조선", r"조선|해양|중공업"),
    ("원전", r"원전|원자력|핵"),
    ("태양광", r"태양광|신재생|수소|풍력"),
    ("전기차", r"전기차|EV|모터|충전|부품"),
    ("철강소재", r"철강|금속|소재|케미칼|화학|신소재"),
    ("건설", r"건설|건축|엔지니어링|토목"),
    ("금융", r"은행|증권|보험|캐피탈|금융|지주"),
    ("유통", r"유통|마트|편의점|백화점|쇼핑"),
    ("식음료", r"식품|음료|제과|주류|농업"),
    ("운송", r"해운|항공|물류|택배"),
    ("보안", r"보안|시큐리티|인증"),
]

SECTOR_MAP = {
    "반도체와반도체장비": "반도체",
    "제약": "바이오",
    "생물공학": "바이오",
    "자동차부품": "전기차",
    "화학": "철강소재",
    "금속": "철강소재",
    "IT서비스": "AI",
    "소프트웨어": "AI",
    "전자장비와기기": "반도체",
    "전자제품": "반도체",
    "건축자재": "건설",
    "건설": "건설",
    "은행": "금융",
    "증권": "금융",
    "보험": "금융",
    "식품": "식음료",
    "음료": "식음료",
    "운송": "운송",
    "항공화물운송과물류": "운송",
    "조선": "조선",
    "우주항공과국방": "방산",
    "게임": "게임",
    "엔터테인먼트": "미디어",
    "방송과엔터테인먼트": "미디어",
    "화장품": "화장품",
    "에너지": "태양광",
    "전기장비": "태양광",
    "유통": "유통",
    "백화점과일반상점": "유통",
}


_ENV_PATH = "/home/jhshi/analyist_dd/.env"


def _load_env(path: str = _ENV_PATH) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _pg_host() -> str:
    host = os.environ.get("POSTGRES_HOST") or "127.0.0.1"
    if host == "postgres" and not os.path.exists("/.dockerenv"):
        return "127.0.0.1"
    return host


def _pg_port() -> int:
    port = int(os.environ.get("POSTGRES_PORT", "5434"))
    if port == 5432 and not os.path.exists("/.dockerenv"):
        return 5434
    return port


def connect():
    _load_env()
    return psycopg2.connect(
        host=_pg_host(),
        port=_pg_port(),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def ensure(cur) -> None:
    cur.execute("CREATE TABLE IF NOT EXISTS themes ("
                " id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, source TEXT,"
                " created_at TIMESTAMPTZ DEFAULT now())")
    cur.execute("CREATE TABLE IF NOT EXISTS stock_themes ("
                " stock_code TEXT NOT NULL, theme TEXT NOT NULL, confidence NUMERIC,"
                " source TEXT, updated_at TIMESTAMPTZ DEFAULT now(),"
                " PRIMARY KEY (stock_code, theme))")
    cur.execute("CREATE TABLE IF NOT EXISTS theme_daily ("
                " theme TEXT NOT NULL, trade_date DATE NOT NULL, members INT,"
                " avg_ret_pct NUMERIC, up_ratio NUMERIC,"
                " PRIMARY KEY (theme, trade_date))")


def build_map(cur) -> int:
    cur.execute("SELECT stock_code, stock_name, sector FROM stocks "
                "WHERE sector NOT IN ('ETF','ETN') AND stock_name IS NOT NULL")
    rows = cur.fetchall()
    compiled = [(name, re.compile(pat)) for name, pat in THEME_RULES]

    cur.execute("DELETE FROM stock_themes")
    payload = []
    theme_names = set()
    for code, name, sector in rows:
        hits = [(t, 1.0) for t, rx in compiled if rx.search(name or "")]
        if not hits and sector in SECTOR_MAP:
            hits = [(SECTOR_MAP[sector], 0.6)]
        for theme, conf in hits:
            payload.append((code, theme, conf, "name" if conf >= 1.0 else "sector"))
            theme_names.add(theme)
    if payload:
        cur.executemany("INSERT INTO stock_themes (stock_code, theme, confidence, source) "
                        "VALUES (%s, %s, %s, %s) ON CONFLICT (stock_code, theme) DO UPDATE "
                        "SET confidence = EXCLUDED.confidence, updated_at = now()",
                        payload)
    if theme_names:
        cur.executemany("INSERT INTO themes (name, source) VALUES (%s, %s) "
                        "ON CONFLICT (name) DO NOTHING",
                        [(theme, "rule") for theme in sorted(theme_names)])
    return len(payload)


def build_daily(cur, days: int) -> int:
    cur.execute("SELECT max(trade_date) FROM stock_prices")
    last = cur.fetchone()[0]
    if last is None:
        return 0
    cur.execute("""
        INSERT INTO theme_daily (theme, trade_date, members, avg_ret_pct, up_ratio)
        SELECT st.theme, p.trade_date, count(*),
               round(avg((p.close_price / NULLIF(p.prev_close, 0) - 1) * 100)::numeric, 4),
               round(avg(CASE WHEN p.close_price > p.prev_close THEN 1 ELSE 0 END)::numeric, 4)
        FROM (
            SELECT stock_code, trade_date, close_price,
                   lag(close_price) OVER (PARTITION BY stock_code ORDER BY trade_date) AS prev_close
            FROM stock_prices
            WHERE trade_date > %s::date - (%s || ' days')::interval
        ) p
        JOIN stock_themes st ON st.stock_code = p.stock_code
        WHERE p.prev_close IS NOT NULL AND p.prev_close > 0
        GROUP BY st.theme, p.trade_date
        ON CONFLICT (theme, trade_date) DO UPDATE
        SET members = EXCLUDED.members, avg_ret_pct = EXCLUDED.avg_ret_pct,
            up_ratio = EXCLUDED.up_ratio
    """, (last, str(days)))
    return cur.rowcount


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    conn = connect()
    cur = conn.cursor()
    try:
        ensure(cur)
        pairs = build_map(cur)
        conn.commit()
        daily = build_daily(cur, args.days)
        conn.commit()
        cur.execute("SELECT count(*) FROM themes")
        themes = cur.fetchone()[0]
        cur.execute("SELECT count(DISTINCT stock_code) FROM stock_themes")
        codes = cur.fetchone()[0]
        cur.execute("SELECT theme, trade_date, members, avg_ret_pct, up_ratio FROM theme_daily "
                    "ORDER BY trade_date DESC, avg_ret_pct DESC LIMIT 5")
        top = cur.fetchall()
        print("테마 {0}개 / 종목 {1}개 / 매핑 {2}건 / theme_daily {3}행".format(
            themes, codes, pairs, daily))
        for row in top:
            print("  ", row)
    finally:
        cur.close()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
