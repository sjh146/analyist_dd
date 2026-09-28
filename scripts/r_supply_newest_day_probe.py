"""읽기 전용 프로브: foreign_institutional 최신 거래일 커버리지 실측.

배경: 일별 크론(kis_supply.sh)은 자체 유니버스로, 이력 확장 러너(R3)는
supply_universe_800.txt 로 수집한다. 두 경로의 최신일 겹침을 수치로 확인한다.
DB 쓰기 없음. 사용: /usr/bin/python3 scripts/r_supply_newest_day_probe.py
"""
import os
import psycopg2

conn = psycopg2.connect(
    host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    port=int(os.environ.get("POSTGRES_PORT", "5434")),
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
)
cur = conn.cursor()

cur.execute(
    "select d, count(*) from (select stock_code, max(trade_date) as d "
    "from foreign_institutional group by stock_code) t group by d order by d desc"
)
rows = cur.fetchall()
print("== 종목별 최신 보유일 분포 (상위 8) ==")
for d, n in rows[:8]:
    print(f"  {d}  {n}종목")
print("  총 종목수", sum(n for _, n in rows))

cur.execute(
    "select trade_date, count(distinct stock_code) from foreign_institutional "
    "where trade_date >= date '2026-09-15' group by 1 order by 1 desc"
)
print("== 일자별 종목수 ==")
for d, n in cur.fetchall():
    print(f"  {d}  {n}")

# 최신일 결측 종목의 직전 보유일 분포
cur.execute("select max(trade_date) from foreign_institutional")
newest = cur.fetchone()[0]
cur.execute(
    "select m.d from (select stock_code, max(trade_date) as d from foreign_institutional "
    "group by stock_code) m where m.d < %s order by m.d desc limit 5",
    (newest,),
)
print(f"== {newest} 결측 종목의 직전 보유일(상위 5) ==")
for (d,) in cur.fetchall():
    print("  ", d)
conn.close()
