"""읽기 전용: 일별 러너의 유동성 유니버스 '앞 250종목'과 최신일 보유 종목의 겹침 실측.

가설 검증: 9/28 데이터를 가진 250종목 = 유동성 상위 250종목(러너가 매 실행 처리하는 구간).
겹침이 높으면 결함의 원인이 '진행파일 날짜창 리셋 + 호출 상한'임이 확정된다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
os.environ.setdefault("PROJ_DIR", PROJ)

import psycopg2  # noqa: E402
import kis_supply_backfill as ks  # noqa: E402

conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
                        port=int(os.environ.get("POSTGRES_PORT", "5434")),
                        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
                        password=os.environ["POSTGRES_PASSWORD"])
cur = conn.cursor()
cur.execute("SELECT MAX(trade_date) FROM market_data")
newest = cur.fetchone()[0]
cur.execute("SELECT stock_code FROM foreign_institutional WHERE trade_date = %s", (newest,))
fresh = {r[0] for r in cur.fetchall()}

uni = [c for c, _ in ks.liquidity_universe(conn, 800)]
head = uni[:250]
tail = uni[250:]
print(f"유동성 유니버스 {len(uni)}종목 / {newest} 보유 {len(fresh)}종목")
print(f"  유동성 상위 250 ∩ 최신일 보유 = {len(fresh & set(head))} / 250 "
      f"({100.0*len(fresh & set(head))/250:.1f}%)")
print(f"  유동성 250 밖 ∩ 최신일 보유 = {len(fresh & set(tail))} / {len(tail)} "
      f"({100.0*len(fresh & set(tail))/max(1,len(tail)):.1f}%)")
print("  → 앞 구간이 최신일을 독식하면 원인은 '날짜창 진행파일 리셋 + 호출 상한'이다.")
conn.close()
