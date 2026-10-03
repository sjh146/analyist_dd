#!/usr/bin/env python3
"""SNS/뉴스 원천 ↔ 패널(prod200) 종목 커버리지 진단 (읽기 전용).

동기(CG73, 2026-10-03): "sns_posts 384,821행/309종목 → 패널 교집합 22종목 ·
news_events 7,133행/211종목 → 패널 교집합 12종목" 이라 기록됐다. 309종목 원천이
200종목 패널과 22개만 겹친다는 것은 '수집 범위' 문제일 수도 있고 **식별자/조인 형식
불일치(배선 결함)** 일 수도 있다. CG67(공시 카운트 전행 0 → 원천엔 42% 커버리지)과
같은 유형의 함정을 배제하기 위해, 종목코드 형식·시장구성·교집합을 직접 찍는다.

판정: 식별자가 같은 형식(예: 6자리 숫자)이고 원천 시장구성이 패널과 유사한데
교집합이 낮으면 '수집 범위' 문제(리서처 결정). 형식이 다르면 '조인 결함'(이 역할 수리).
"""
import os
import zipfile

import numpy as np
import psycopg2


def conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", "stock_password"),
    )


def panel_codes(path):
    z = zipfile.ZipFile(path)
    codes = np.load(z.open("codes.npy"), allow_pickle=True)
    return [str(c) for c in codes]


def show(cur, label, sql):
    try:
        cur.execute(sql)
        rows = cur.fetchall()
    except Exception as e:
        print(f"[{label}] ERR {e}")
        cur.connection.rollback()
        return None
    print(f"[{label}] rows={len(rows)}")
    for r in rows[:8]:
        print("   ", r)
    return rows


def main():
    path = "services/xgboost-ml/app/models/wf/panel_prod200.npz"
    if not os.path.exists("/app/app/models/wf/panel_prod200.npz"):
        path = "/app/app/models/wf/panel_prod200.npz"
    pcodes = panel_codes(path if os.path.exists(path) else "/app/app/models/wf/panel_prod200.npz")
    pset = set(pcodes)
    print(f"panel codes n={len(pset)} sample={sorted(pset)[:6]}")

    c = conn()
    cur = c.cursor()

    for tbl, col, dcol in (
        ("sns_posts", "stock_code", "created_at"),
        ("news_events", "stock_code", "event_date"),
        ("stock_sentiment", "stock_code", "analysis_date"),
        ("disclosures", "stock_code", "rcept_dt"),
    ):
        rows = show(cur, f"{tbl}.{col} (top by count)", f"""
            SELECT {col}, count(*) FROM {tbl}
            GROUP BY {col} ORDER BY count(*) DESC LIMIT 5
        """)
        cur.execute(f"SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL")
        codes = [str(r[0]) for r in cur.fetchall()]
        cset = set(codes)
        inter = pset & cset
        print(f"  -> {tbl}: distinct={len(cset)} · panel∩={len(inter)} · "
              f"len_sample={[len(x) for x in codes[:3]]} · sample={codes[:5]}")
        # 형식 힌트: 6자리 숫자 비율
        six = sum(1 for x in codes if len(x) == 6 and x.isdigit())
        print(f"     6자리숫자 {six}/{len(codes)}")

    # 원천별 날짜 범위
    show(cur, "sns_posts 기간", "SELECT min(created_at), max(created_at), count(*) FROM sns_posts")
    show(cur, "news_events 기간", "SELECT min(event_date), max(event_date), count(*) FROM news_events")
    c.close()


if __name__ == "__main__":
    main()
