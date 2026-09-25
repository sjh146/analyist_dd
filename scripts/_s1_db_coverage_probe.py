"""DB 시계열 커버리지 실측: 표본 기간 확대(days 420 → 1200+)가 가능한가?

가설 S1(다음 레버 후보): 학습 표본이 폴드당 1,230행뿐이라 depth 얕을수록 AUC 가 단조 증가
(d2 0.5477 > d3 0.5469 > d4 0.5412 > d6 0.5344) — 전형적 소표본 과적합 신호.
피처를 더 넣는 축은 이미 4번 실패했으므로, 남은 축은 '행 수'다.
→ 가격/피처 원천이 몇 년치 있는지부터 확인한다(없으면 이 가설은 즉시 폐기).
"""
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import extra_experiments as ex  # noqa: E402

ml = ex._load_driver()
pg = ml.connect_pg()
cur = pg.cursor()
try:
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND (table_name ILIKE '%%price%%'
              OR table_name ILIKE '%%daily%%' OR table_name ILIKE '%%ohlc%%'
              OR table_name ILIKE '%%stock%%' OR table_name ILIKE '%%feature%%')
        ORDER BY table_name
    """)
    tabs = [r[0] for r in cur.fetchall()]
    print("후보 테이블:", tabs)
    for t in tabs:
        try:
            cur.execute(f'SELECT count(*), min(date), max(date) FROM "{t}"')
            n, mn, mx = cur.fetchone()
            print(f"  {t:34s} rows={n:>10} {mn} ~ {mx}")
        except Exception as e:
            pg.rollback()
            print(f"  {t:34s} (date 컬럼 없음/오류: {type(e).__name__})")
            try:
                cur.execute(f'SELECT count(*) FROM "{t}"')
                print(f"      rows={cur.fetchone()[0]}")
            except Exception as e2:
                pg.rollback()
                print(f"      실패 {type(e2).__name__}")
finally:
    try:
        pg.close()
    except Exception:
        pass
