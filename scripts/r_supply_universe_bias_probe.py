"""읽기 전용: 최신 거래일 커버리지가 '유니버스 순서(앞부분)'에 편중됐는지 실측한다.

가설: 일별 수급 러너(kis_supply_backfill.py)는 진행파일 키가 날짜창이라 날마다 리셋되고,
호출 상한 500콜 ÷ 종목당 2콜 ≈ 250종목만 처리한다 → 유니버스 앞 250종목만 최신일을 갖고
꼬리는 굶는다. 이 스크립트는 그 편중을 비율로 보여준다. DB 쓰기 없음.
"""
import os
import psycopg2

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
UNIVERSE = os.path.join(PROJ, "data", "kis", "supply_universe_800.txt")

conn = psycopg2.connect(
    host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    port=int(os.environ.get("POSTGRES_PORT", "5434")),
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
)
cur = conn.cursor()
cur.execute("SELECT MAX(trade_date) FROM market_data")
newest = cur.fetchone()[0]
cur.execute("SELECT stock_code, MAX(trade_date) FROM foreign_institutional GROUP BY stock_code")
last = dict(cur.fetchall())

codes = [l.strip() for l in open(UNIVERSE) if l.strip()]
head, tail = codes[:250], codes[250:]

def cov(group):
    have = [c for c in group if last.get(c) == newest]
    return len(have), len(group), (100.0 * len(have) / len(group) if group else 0.0)

h_have, h_n, h_pct = cov(head)
t_have, t_n, t_pct = cov(tail)
print(f"최신 거래일(market_data) = {newest}")
print(f"유니버스 파일 {len(codes)}종목 / DB 보유 {len(last)}종목")
print(f"  앞 250종목: 최신일 보유 {h_have}/{h_n} ({h_pct:.1f}%)")
print(f"  뒤 {t_n}종목: 최신일 보유 {t_have}/{t_n} ({t_pct:.1f}%)")

# 거래일 지연 분포 (결번 = market_data 에 있는 날짜 기준)
cur.execute("SELECT DISTINCT trade_date FROM market_data WHERE trade_date > %s ORDER BY 1", (min(d for d in last.values() if d),))
ahead = [r[0] for r in cur.fetchall()]
cur.execute("SELECT DISTINCT trade_date FROM market_data ORDER BY 1")
all_dates = [r[0] for r in cur.fetchall()]
idx = {d: i for i, d in enumerate(all_dates)}
dist = {}
for c, d in last.items():
    lag = idx[newest] - idx[d] if d in idx else -1
    dist[lag] = dist.get(lag, 0) + 1
print("== 종목별 최신일 지연(거래일) 분포 ==")
for lag in sorted(dist):
    print(f"  지연 {lag}거래일: {dist[lag]}종목")
conn.close()
