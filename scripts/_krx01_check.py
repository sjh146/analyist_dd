import os
import psycopg2

conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
                        port=int(os.environ.get("POSTGRES_PORT", 5434)),
                        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
                        user=os.environ.get("POSTGRES_USER", "stock_user"),
                        password=os.environ.get("POSTGRES_PASSWORD", ""))
cur = conn.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%krx%' ORDER BY 1")
print("tables:", [r[0] for r in cur.fetchall()])
for t in ("krx_trading",):
    try:
        cur.execute(f"SELECT count(*), min(trade_date), max(trade_date) FROM {t}")
        print(t, cur.fetchone())
        cur.execute(f"SELECT trade_date, count(*), sum(krx_total_trading_value) FROM {t} "
                    f"GROUP BY trade_date ORDER BY trade_date DESC LIMIT 6")
        for r in cur.fetchall():
            print("  ", r)
    except Exception as exc:
        conn.rollback()
        print(t, "ERR", exc)
cur.close()
conn.close()
