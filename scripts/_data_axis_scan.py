"""데이터 축 전수 스크린(읽기 전용) — 패널 유니버스/구간을 덮는 미사용 원천이 남아 있는가.

CG73(2026-10-04) 이후 원천 테이블이 늘었으므로 재측정한다.
출력: ① DB 테이블별 행수/컬럼수 ② 패널 유니버스(종목) 교집합 ③ 패널 구간 겹침
④ 패널 피처명과 매칭되지 않는 후보 컬럼.
"""
import os
import numpy as np
import psycopg2

PANEL = os.environ.get("PANEL", "/app/app/models/wf/panel_prod200.npz")


def db():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
    )


def main():
    z = np.load(PANEL, allow_pickle=True)
    stocks = None
    for n in ("stocks", "stock_codes", "codes", "stock_code"):
        if n in z:
            stocks = z[n]
            break
    if stocks is None:
        # infer from columns
        for n in z.keys():
            a = z[n]
            if getattr(a, "dtype", None) is not None and a.dtype.kind in "OS" and a.ndim == 1:
                stocks = a
                break
    dates = None
    for n in ("dates", "trade_dates", "date"):
        if n in z:
            dates = z[n]
            break
    feats = None
    for n in ("feature_names", "features_names", "cols", "columns", "features"):
        if n in z:
            feats = z[n]
            break
    print("PANEL:", PANEL)
    print("  keys:", list(z.keys()))
    print("  stocks:", None if stocks is None else len(stocks))
    print("  dates:", None if dates is None else (str(dates[0]), str(dates[-1]), len(dates)))
    print("  feats:", None if feats is None else len(feats))

    if stocks is not None and len(stocks) and not isinstance(stocks[0], (bytes, str)):
        print("  (stocks are not strings)", type(stocks[0]))
    stock_set = set()
    if stocks is not None:
        for s in stocks:
            try:
                stock_set.add(str(s.item()) if hasattr(s, "item") else str(s))
            except Exception:
                pass
    print("  stock_set n:", len(stock_set), "sample:", sorted(stock_set)[:5])

    d0 = d1 = None
    if dates is not None:
        try:
            d0, d1 = str(dates[0])[:10], str(dates[-1])[:10]
        except Exception:
            pass
    print("  window:", d0, "~", d1)

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """select table_name from information_schema.tables
           where table_schema='public' order by table_name"""
    )
    tabs = [r[0] for r in cur.fetchall()]
    print("\nTABLES:", len(tabs))
    for t in tabs:
        try:
            cur.execute(f'select count(*) from public."{t}"')
            n = cur.fetchone()[0]
        except Exception as e:
            conn.rollback()
            print(f"  {t:38s} ERR {e}")
            continue
        info = ""
        # find a code column and a date column
        cur.execute(
            """select column_name, data_type from information_schema.columns
               where table_schema='public' and table_name=%s""",
            (t,),
        )
        cols = cur.fetchall()
        cnames = [c[0] for c in cols]
        cc = next((c for c in cnames if c in ("stock_code", "code", "symbol")), None)
        dc = next((c for c in cnames if "date" in c or c == "dt"), None)
        if cc:
            try:
                if stock_set:
                    cur.execute(f'select count(distinct "{cc}") from public."{t}"')
                    dn = cur.fetchone()[0]
                    cur.execute(
                        f'select count(distinct "{cc}") from public."{t}" '
                        f'where "{cc}" = any(%s)',
                        (sorted(stock_set),),
                    )
                    inter = cur.fetchone()[0]
                    info += f" codes={dn} inter_panel={inter}"
            except Exception as e:
                conn.rollback()
        if dc:
            try:
                cur.execute(f'select min("{dc}"), max("{dc}") from public."{t}"')
                mn, mx = cur.fetchone()
                info += f" dates={mn}~{mx}"
            except Exception as e:
                conn.rollback()
        print(f"  {t:38s} rows={n:>9d} ncols={len(cols):>3d}{info}")
    conn.close()


if __name__ == "__main__":
    main()
