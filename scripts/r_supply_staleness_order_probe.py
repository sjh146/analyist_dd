"""읽기 전용 검증: staleness_order 가 실제 유니버스에서 '굶는 꼬리'를 앞으로 당기는가.

수정 전: 실행마다 유니버스 앞 250종목만 최신일을 가져 꼬리(280종목 지연 1거래일,
최대 4거래일)가 굶었다(실측 2026-09-29 00:1x). 수정 후: 최신일이 오래된 순으로 정렬해
상한(250종목)에 잘려도 가장 뒤처진 종목부터 채운다. DB 쓰기·네트워크 없음.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
os.environ.setdefault("PROJ_DIR", PROJ)

import psycopg2  # noqa: E402
import kis_supply_backfill as ks  # noqa: E402

codes = [l.strip() for l in open(os.path.join(PROJ, "data", "kis", "supply_universe_800.txt")) if l.strip()]
conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
                        port=int(os.environ.get("POSTGRES_PORT", "5434")),
                        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
                        password=os.environ["POSTGRES_PASSWORD"])
cur = conn.cursor()
cur.execute("SELECT stock_code, MAX(trade_date) FROM foreign_institutional GROUP BY stock_code")
last = dict(cur.fetchall())
newest = max(last.values()) if last else None

todo = [(c, "") for c in codes]
ordered = ks.staleness_order(conn, todo)
CAP = 250  # 실행당 호출 상한 500콜 ÷ 종목당 2콜

print("== 수정 후 정렬 결과 (앞 12종목) ==")
for c, _ in ordered[:12]:
    print(f"  {c}  최신 보유일 {last.get(c)}")

picked = [c for c, _ in ordered[:CAP]]
picked_fresh = sum(1 for c in picked if last.get(c) == newest)
print(f"== 상한 {CAP}종목이 처리하는 대상 ==")
print(f"  그중 이미 최신일 보유: {picked_fresh}종목 (수정 전에는 이 자리를 앞 250종목이 독식)")
print(f"  아직 뒤처진 종목을 처리: {CAP - picked_fresh}종목")
stale_all = [c for c in codes if last.get(c) != newest]
print(f"  유니버스 내 최신일 미보유: {len(stale_all)}종목 → 다음 1회 실행으로 "
      f"{min(len(stale_all), CAP - picked_fresh)}종목이 따라잡는다")
conn.close()
