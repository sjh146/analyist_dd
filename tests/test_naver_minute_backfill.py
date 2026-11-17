"""docs/spec_minute_replay.md '테스트' 절 계약 4개 — DB·네트워크 없이 합성 응답으로 검증.

① 응답 파싱(종가·누적거래량, OHLC null 무시)  ② 정규장(09:00~15:30) 밖 행 제외
③ UPSERT 멱등(재실행 시 신규 행 0 — sqlite3 in-memory 로 ON CONFLICT 계약 검증)
④ 빈 응답 종목 카운트(전 창 0행 → codes_empty)

백필 순수 함수(parse_response·store_rows·backfill_once)만 검증한다 — 실제 Naver 호출 없음.
배치 SQL 은 PostgreSQL %s 플레이스홀더 그대로 두고, 테스트는 in-memory sqlite 에 맞게
? 로 치환만 해서 동일 문장의 ON CONFLICT ... DO NOTHING 멱등 계약을 확인한다.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import naver_minute_backfill as nb  # noqa: E402


# ① 응답 파싱 — 종가·누적거래량만 사용하고 OHLC null 은 무시(스펙 §1)
def test_parse_response_close_cumvol_only():
    # 실측 형식(2026-11-18): 단일 최상위 배열 안에 헤더행(홑따옴표) + 데이터행(쌍따옴표,
    #   종가·누적거래량은 숫자) — OHLC 는 null. "'"→'"' 치환 없이는 json.loads 가 실패하므로
    #   parse_response 가 치환 후 파싱한다.
    text = ("\n[['날짜','시가','고가','저가','종가','거래량','외국인소진율'],"
            '["202611101530", null, null, null, 103200, 123456, null]]\n')
    raw_n, parsed = nb.parse_response(text)
    assert raw_n == 1                       # 소스가 준 데이터 행수(자기신고 source)
    assert parsed == [("2026-11-10", "1530", 103200.0, 123456.0)]   # 종가·누적거래량만
    # 손상/비JSON 응답 → 빈 결과(부분 성공 허용 — 실패가 아니라 빈 취급)
    assert nb.parse_response("not json") == (0, [])
    # 헤더행만 있는 빈 응답(실측: 과거 창 70B EUC-KR) → 0행
    assert nb.parse_response("\n[['날짜','시가','고가','저가','종가','거래량','외국인소진율']]") \
        == (0, [])


# ② 정규장 필터 — 09:00~15:30 만 저장(장전·시간외 제외, 소스 행수는 그대로 센다)
def test_parse_response_session_filter():
    rows = ["202611100859", "202611100900", "202611101200", "202611101530",
            "202611101531", "202611101959"]
    text = ("\n[['날짜','시가','고가','저가','종가','거래량','외국인소진율'],"
            + ",".join('["%s", null, null, null, 100, 10, null]' % r for r in rows)
            + "]\n")
    raw_n, parsed = nb.parse_response(text)
    assert raw_n == 6                       # 원천 행수는 필터 전 전부(자기신고 3분리)
    assert [p[1] for p in parsed] == ["0900", "1200", "1530"]   # 장전 0859·시간외 제외
    assert all(p[0] == "2026-11-10" for p in parsed)


# ③ UPSERT 멱등 — 같은 (종목,일,분) 재실행 시 신규 행 0(ON CONFLICT DO NOTHING)
def test_store_rows_upsert_idempotent(monkeypatch):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE minute_close_bars (stock_code TEXT, trade_date TEXT, time TEXT,"
        " close_price REAL, cum_volume REAL, source TEXT,"
        " PRIMARY KEY (stock_code, trade_date, time))")
    # 배치(PostgreSQL)는 %s 그대로 — in-memory sqlite 검증용으로 ? 로만 치환
    monkeypatch.setattr(nb, "UPSERT_SQL", nb.UPSERT_SQL.replace("%s", "?"))
    rows = [("005930", "2026-11-10", "0900", 57000.0, 1000.0, nb.SOURCE),
            ("005930", "2026-11-10", "0901", 57100.0, 2000.0, nb.SOURCE),
            ("096530", "2026-11-10", "0900", 24200.0, 500.0, nb.SOURCE)]
    assert nb.store_rows(conn, rows) == 3   # 첫 적재 — 3행 신규
    assert nb.store_rows(conn, rows) == 0   # 재실행 — 전부 중복 → 신규 0(멱등)
    n = conn.execute("SELECT COUNT(*) FROM minute_close_bars").fetchone()[0]
    assert n == 3
    conn.close()


# ④ 빈 응답 종목 카운트 — 전 창 0행인 종목만 codes_empty 로 센다(부분 성공 허용)
def test_backfill_once_counts_empty_codes():
    def fetch(code, s, e):
        if code == "A":                     # 과거 창 → 헤더만(빈 응답, 실측)
            return (0, [])
        return (2, [("2026-11-10", "0900", 100.0, 10.0),
                    ("2026-11-10", "0901", 101.0, 20.0)])
    written = []
    stats = nb.backfill_once(
        fetch, ["A", "B"],
        [["2026-11-10", "2026-11-12", "2026-11-13"]],
        lambda rows: written.append(rows) or len(rows))
    assert stats["rows_received"] == 2      # 소스가 준 행수(자기신고 source)
    assert stats["rows_parsed"] == 2        # 파서가 만든 정규장 행수(claimed)
    assert stats["rows_written"] == 2       # 실제 저장 신규 행수(persisted)
    assert stats["codes_empty"] == 1        # A 만 빈 응답 — B 는 아님
    assert written == [[("B", "2026-11-10", "0900", 100.0, 10.0, nb.SOURCE),
                        ("B", "2026-11-10", "0901", 101.0, 20.0, nb.SOURCE)]]
