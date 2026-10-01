#!/usr/bin/env python3
"""_sentiment_asof_test.py — 감성 시계열 조회의 시점정합(as-of)·정렬 계약 자체점검 (CG60).

왜(2026-10-02 실측): `SentimentFeatures.get_sentiment_from_db` 는 종전에 날짜 필터가 없어
**빌드 시점의 DB 내용**을 그대로 읽었다 → 과거 패널 날짜에 미래 감성행이 들어가는 시점누수 +
같은 (종목·날짜) 값이 패널을 구운 날짜에 따라 달라지는 재현 불가(panel_995 news_count_5d 비영
0.00% vs panel_420_asofpatch_evfix 97.94% — 같은 종목집합·같은 소스).

같이 수리한 것: `get_aggregate_sentiment` 는 `scores[-5:]`(최근 5행)·`scores[-1]-scores[0]`(추세)로
쓰는데 조회가 DESC 를 그대로 넘겨 **창의 가장 오래된 5행** 합이 'news_count_5d' 가 되고 추세 부호가
뒤집혀 있었다(6행 실측: 최신 5행 합 20 이어야 할 값이 25).

검증:
  1. as_of 를 주면 SQL 에 `analysis_date <=` 가 들어가고 파라미터에 기준일이 실린다.
  2. as_of 를 주지 않으면 **종전 SQL 그대로**(다른 호출자 회귀 0 — 추론 경로 호환).
  3. 누수 차단: 소스에 미래(09-30) 행만 있고 date=2024-01-08 이면 news_count_5d == 0.
     (수리 전에는 09-30 행을 읽어 비영이었다 — 이 검사가 수리의 핵심 증거다)
  4. date=None 이면 as_of 없이 조회(추론 경로 비트 동일).
  5. 반환 키 집합이 수리 전후 동일(피처 계약 불변 — 11키).
  6. 정렬 계약: 최신 5행 합 = news_count_5d · 추세 = 최신 − 최과거(부호 포함).

실행: docker exec stock_xgboost_ml python /app/scripts/_sentiment_asof_test.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from app.feature_engine.sentiment_features import SentimentFeatures  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSES if cond else FAILS).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


class FakeCursor:
    """stock_sentiment 를 흉내낸다 — 실제 DB 와 같이 DESC(최신 우선) + LIMIT 으로 돌려준다."""

    def __init__(self, rows, sink):
        self.rows = rows              # [(date, avg, cnt, pos, neg, neu, auth), ...] DESC
        self.sql = ""
        self.params = None
        self.sink = sink

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.params = params
        self.sink.append((self.sql, params))

    def fetchall(self):
        if "analysis_date <=" in self.sql:
            cut, days = self.params[1], self.params[2]
            return [r for r in self.rows if str(r[0]) <= str(cut)][: int(days)]
        days = self.params[1] if len(self.params) > 1 else 20
        return self.rows[: int(days)]

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.sqls: list = []

    def cursor(self):
        return FakeCursor(self.rows, self.sqls)

    def sentiment_sql(self):
        return [s for s in self.sqls if "FROM stock_sentiment" in s[0]]

    def rollback(self):
        pass


EXPECTED_KEYS = {
    "sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d", "sentiment_trend",
    "sentiment_volatility", "news_count_5d", "news_count_20d", "authenticity_avg",
    "positive_ratio", "negative_ratio", "disclosure_count_5d",
}


def main() -> int:
    s = SentimentFeatures()

    print("== 1. as_of 를 주면 SQL 에 날짜 필터")
    conn = FakeConn([("2026-09-30", 0.5, 7, 3, 2, 2, 0.8)])
    s.get_sentiment_from_db("005930", conn, as_of="2026-07-03")
    sql, params = conn.sentiment_sql()[0]
    check("SQL 에 analysis_date <= 포함", "analysis_date <=" in sql, sql[:80])
    check("파라미터에 기준일 실림", params[1] == "2026-07-03", str(params))

    print("== 2. as_of 없으면 종전 SQL(회귀 0)")
    conn = FakeConn([("2026-09-30", 0.5, 7, 3, 2, 2, 0.8)])
    s.get_sentiment_from_db("005930", conn)
    sql, params = conn.sentiment_sql()[0]
    check("날짜 필터 없음", "analysis_date <=" not in sql, sql[:80])
    check("파라미터 (코드, 20)", params == ("005930", 20), str(params))

    print("== 3. 누수 차단 — 소스에 미래 행만 있으면 과거 날짜는 0")
    future_only = [("2026-10-01", 0.4, 9, 4, 3, 2, 0.7), ("2026-09-30", 0.5, 7, 3, 2, 2, 0.8)]
    feats = s.get_all_features("000250", FakeConn(future_only), date="2024-01-08")
    check("news_count_5d == 0 (미래 행 미사용)", feats["news_count_5d"] == 0,
          str(feats["news_count_5d"]))
    check("news_count_20d == 0", feats["news_count_20d"] == 0, str(feats["news_count_20d"]))
    check("sentiment_avg == 0.0", feats["sentiment_avg"] == 0.0, str(feats["sentiment_avg"]))
    # 수리 전 동작(필터 없음)을 그대로 재현하면 미래 행을 읽는다 — 두 경로가 다름을 증명
    legacy = s.get_all_features("000250", FakeConn(future_only), date=None)
    check("대조: as_of 없는 경로는 미래 행을 읽는다(수리 전 동작 재현)",
          legacy["news_count_5d"] > 0, str(legacy["news_count_5d"]))

    print("== 4. date=None 이면 as_of 없이 조회(추론 경로 비트 동일)")
    conn = FakeConn([("2026-10-01", 0.5, 3, 1, 1, 1, 0.9)])
    f_now = s.get_all_features("005930", conn, date=None)
    sql, _ = conn.sentiment_sql()[0]
    check("감성 SQL 에 날짜 필터 없음", "analysis_date <=" not in sql, sql[:80])
    check("값 정상(3행 합)", f_now["news_count_5d"] == 3, str(f_now["news_count_5d"]))

    print("== 5. 피처 키 계약 불변(11키)")
    check("키 집합 동일", set(feats) == EXPECTED_KEYS, f"차이={set(feats) ^ EXPECTED_KEYS}")

    print("== 6. 정렬 계약 — 최신 5행 합 · 추세 부호")
    # DESC(최신 우선) 6행: 07-03=2, 07-02=3, 07-01=4, 06-30=5, 06-29=6, 06-28=7
    rows = [("2026-07-03", 0.1, 2, 1, 1, 0, 0.5), ("2026-07-02", 0.2, 3, 1, 2, 0, 0.6),
            ("2026-07-01", 0.3, 4, 2, 1, 1, 0.7), ("2026-06-30", 0.4, 5, 2, 3, 0, 0.8),
            ("2026-06-29", 0.5, 6, 3, 2, 1, 0.9), ("2026-06-28", 0.6, 7, 4, 2, 1, 0.4)]
    f_win = s.get_all_features("005930", FakeConn(rows), date="2026-07-03")
    check("news_count_5d == 20 (최신 5행 2+3+4+5+6)", f_win["news_count_5d"] == 20,
          str(f_win["news_count_5d"]))
    check("news_count_20d == 27 (전체 6행)", f_win["news_count_20d"] == 27,
          str(f_win["news_count_20d"]))
    check("sentiment_avg == 0.35", abs(f_win["sentiment_avg"] - 0.35) < 1e-9,
          str(f_win["sentiment_avg"]))
    check("sentiment_trend == -0.5 (최신 0.1 − 최과거 0.6)",
          abs(f_win["sentiment_trend"] - (-0.5)) < 1e-9, str(f_win["sentiment_trend"]))

    print(f"\n총 {len(PASSES)} PASS / {len(FAILS)} FAIL")
    for f in FAILS:
        print("  FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
