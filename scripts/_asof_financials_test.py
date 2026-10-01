#!/usr/bin/env python3
"""재무 피처의 **시점정합(as-of) 회귀 테스트** (순수 파이썬 — 이 스택엔 pytest 가 없다).

배경(2026-10-02 실측): `FactorFeatures.get_all_factors` 와 `QualityScorer.get_f_score` 가
date 인자를 받지 않아 `financial_statements` 를 `ORDER BY report_date DESC LIMIT n` 으로 읽었다
→ 과거 행에도 빌드 시점 최신 보고서가 들어갔다. 패널 실측으로 종목당 유니크값 1(종목 상수):
value_per · value_pbr · value_psr · value_pcr · value_ncav · quality_roa · quality_f_score ·
quality_asset_growth · quality_score. 패널 스크린 단일피처 AUC 상위 3개가 정확히 이 컬럼들이었고
(quality_roa 0.5534 · quality_score 0.5507 · value_per 0.5454) 종목상수 피처가 top30 선별을
지배했다 = 누수 게이트 ②·③ 위반.

검사 5종:
 A. SQL 계약(스텁): date 를 주면 as-of 술어가 있고, 주지 않으면 종전 SQL 그대로(back-compat).
 B. 술어 산술(순수): 연간 보고서(report_date = 연초)는 90일, 그 밖은 45일 지연.
 C. 라이브: 지연 규칙을 만족하는 보고서만 나온다(가장 이른 보고서 시점에는 **아무 행도 안 나온다**).
 D. 라이브: 기준일을 늘리면 참조 보고서가 같거나 최신(단조) — 누수가 있으면 뒤집힌다.
 E. 라이브: 시총 as-of = 현재 시총 × (그날 종가 / 최신 종가).

사용: docker exec stock_xgboost_ml python /app/scripts/_asof_financials_test.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, "/app")

FAIL: list[str] = []
NOTE: list[str] = []
NPASS = [0]


def check(cond: bool, msg: str) -> None:
    if cond:
        NPASS[0] += 1
        print(f"  PASS {msg}")
    else:
        print(f"  FAIL {msg}")
        FAIL.append(msg)


def note(msg: str) -> None:
    NOTE.append(msg)
    print(f"  NOTE {msg}")


# ---------------------------------------------------------------- 스텁 DB
class _Cur:
    def __init__(self, rows=None, one=None):
        self.sql = None
        self.params = None
        self._rows = rows if rows is not None else []
        self._one = one

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._one

    def close(self):
        pass


class _Conn:
    def __init__(self, cur: _Cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def rollback(self):
        pass


def main() -> int:
    from app.feature_engine.factor_features import (
        ASOF_ANNUAL_DELAY_DAYS,
        ASOF_DELAY_DAYS,
        FactorFeatures,
        asof_report_predicate,
    )
    from app.feature_engine.scorer import QualityScorer

    print("== A. SQL 계약(스텁) ==")
    pred = asof_report_predicate()
    check("report_date +" in pred and "%s::date" in pred, "술어에 report_date + … <= %s::date 가 있다")
    check(f"INTERVAL '{ASOF_ANNUAL_DELAY_DAYS} days'" in pred and
          f"INTERVAL '{ASOF_DELAY_DAYS} days'" in pred, "연간 90일 / 그 밖 45일 분기가 있다")

    ff = FactorFeatures()
    for name, fn in (
        ("_get_financials", lambda c: ff._get_financials("000250", c, date="2026-03-31")),
        ("_get_annual_financials", lambda c: ff._get_annual_financials("000250", c, date="2026-03-31")),
        ("_get_earnings_history", lambda c: ff._get_earnings_history("000250", c, date="2026-03-31")),
    ):
        cur = _Cur()
        try:
            fn(_Conn(cur))
        except Exception as e:  # 컬럼 프로브 캐시 등으로 예외가 나도 SQL 계약만 본다
            note(f"{name}: 예외 {type(e).__name__} (SQL 계약 검사는 계속)")
        sql = (cur.sql or "")
        check("report_date +" in sql and "%s::date" in sql, f"{name}: date 를 주면 as-of 술어가 SQL 에 있다")
        check(cur.params is not None and "2026-03-31" in tuple(cur.params),
              f"{name}: 기준일이 파라미터로 전달된다")

    # back-compat: date 없음 → as-of 술어 없음
    cur = _Cur()
    ff._get_financials("000250", _Conn(cur))
    check("INTERVAL" not in (cur.sql or ""), "_get_financials(date=None): as-of 술어 없음(종전 SQL)")

    cur = _Cur(one=None)
    QualityScorer().get_f_score("000250", _Conn(cur), date="2026-03-31")
    check("report_date +" in (cur.sql or ""), "get_f_score(date=…): as-of 술어가 SQL 에 있다")
    cur = _Cur(one=None)
    QualityScorer().get_f_score("000250", _Conn(cur))
    check("INTERVAL" not in (cur.sql or ""), "get_f_score(date=None): 종전 SQL 그대로(회귀)")

    print("== C/D/E. 라이브 DB ==")
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, count(*) n, min(report_date), max(report_date)
        FROM financial_statements GROUP BY stock_code HAVING count(*) >= 2
        ORDER BY n DESC LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        note("financial_statements 에 보고서 2건 이상 종목이 없다 — 라이브 검사 생략")
    else:
        code, n, rmin, rmax = row
        note(f"표본 종목 {code}: 보고서 {n}건 {rmin}~{rmax}")

        # C. 가장 이른 보고서 '당일'에는 지연(45일) 전이므로 어떤 보고서도 쓸 수 없어야 한다.
        latest_c, _ = ff._get_financials(code, conn, date=str(rmin))
        check(not latest_c, f"기준일 {rmin}(최초 보고서 당일): as-of 결과 없음(지연 미충족·누수 차단)")
        # 최초 보고서 + 46일이면 그 보고서를 쓸 수 있다.
        d_after_first = (rmin + dt.timedelta(days=ASOF_DELAY_DAYS + 1)).isoformat()
        got, _ = ff._get_financials(code, conn, date=d_after_first)
        check(bool(got), f"기준일 {d_after_first}(+{ASOF_DELAY_DAYS + 1}일): 그 보고서를 쓴다")

        # D. 단조성 + 종전 경로와의 차이
        none_latest, _ = ff._get_financials(code, conn, date=None)
        early, _ = ff._get_financials(code, conn, date=d_after_first)
        if got and none_latest and early.get("report_date") and none_latest.get("report_date"):
            check(early["report_date"] <= none_latest["report_date"],
                  f"단조: as-of 보고서({early['report_date']}) <= 최신({none_latest['report_date']})")
            check(str(early["report_date"]) != str(none_latest["report_date"]) or n == 1,
                  "as-of 가 최신 보고서와 다르다(누수 제거 실증)")

        # E. 시총 as-of = 현재 시총 × 종가비
        cur.execute("""
            SELECT trade_date::text, close_price FROM market_data
            WHERE stock_code = %s ORDER BY trade_date ASC
        """, (code,))
        px = cur.fetchall()
        if len(px) >= 2:
            import pandas as pd
            mdf = pd.DataFrame({"trade_date": [p[0] for p in px], "close_price": [float(p[1]) for p in px]})
            old_date = px[0][0]
            cap_now = ff._get_market_cap(code, conn)
            cap_old = ff._get_market_cap(code, conn, date=old_date, market_df=mdf)
            want = cap_now * (float(px[0][1]) / float(px[-1][1])) if cap_now else 0.0
            rel = abs(cap_old - want) / want if want else 1.0
            check(want > 0 and rel < 1e-6,
                  f"시총 as-of {cap_old:,.0f} == 현재×종가비 {want:,.0f} (rel {rel:.2e})")
            check(cap_old != cap_now or float(px[0][1]) == float(px[-1][1]),
                  f"과거 시총({cap_old:,.0f}) ≠ 현재 시총({cap_now:,.0f})")
        else:
            note("market_data 가 2행 미만 — 시총 as-of 검사 생략")

    conn.close()

    print()
    if NOTE:
        print("NOTES:")
        for m in NOTE:
            print("  -", m)
    print(f"RESULT: PASS {NPASS[0]} · FAIL {len(FAIL)}")
    if FAIL:
        print("실패 목록:")
        for m in FAIL:
            print("  x", m)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
