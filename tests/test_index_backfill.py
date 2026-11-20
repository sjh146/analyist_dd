"""docs/spec_index_backfill.md '테스트' 절 계약 3개 — DB·네트워크 없이 합성 응답으로 검증.

① 네이버 응답 파싱(작은따옴표 헤더행 + 쌍따옴표 데이터행, 선두 공백·행간 탭)
② UPSERT 멱등(2회 실행 시 행수 불변 — sqlite3 in-memory 로 ON CONFLICT DO UPDATE 계약 검증)
③ 결측 행(빈 값)은 건너뛰고 카운트에 기록

백필 순수 함수(parse_response·store_rows)만 검증한다 — 실제 Naver 호출 없음.
배치 SQL 은 PostgreSQL %s 플레이스홀더 그대로 두고, 테스트는 in-memory sqlite 에 맞게
? 로 치환만 해서 동일 문장의 ON CONFLICT ... DO UPDATE 멱등 계약을 확인한다.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import index_backfill as ib  # noqa: E402


# ① 응답 파싱 — 실측 형식(2026-11-20): 선두 '\n ' + 행간 탭, 헤더행 홑따옴표,
#    데이터행 쌍따옴표·오름차순. ast.literal_eval(원문)은 IndentationError 실측 →
#    parse_response 가 .strip() 후 파싱해야 한다. 7번째 컬럼(외국인소진율)은 저장 안 함.
def test_parse_response_real_format():
    text = ("\n [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],\n\n\t\n\t\t\n"
            '["20261117", 5008.09, 5084.06, 4957.0, 4968.48, 193978, 0.0],\n\t\t\n'
            '["20261120", 4928.51, 4971.31, 4831.69, 4869.96, 193704, 0.0]\n\t\t\n\t\n\t\n\n]\n')
    raw_n, rows, skipped = ib.parse_response(text)
    assert raw_n == 2                       # 소스가 준 데이터 행수(자기신고 source)
    assert skipped == 0
    assert rows == [
        ("2026-11-17", 5008.09, 5084.06, 4957.0, 4968.48, 193978),
        ("2026-11-20", 4928.51, 4971.31, 4831.69, 4869.96, 193704),
    ]
    # 헤더행만 있는 빈 응답(실측: 미래 창 2030 → 헤더만) → 0행
    assert ib.parse_response("\n [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율']]\n") \
        == (0, [], 0)
    # 손상/차단 페이지 응답 → 빈 결과(부분 성공 허용 — 실패가 아니라 빈 취급)
    assert ib.parse_response("<html>blocked</html>") == (0, [], 0)


# ② UPSERT 멱등 — 같은 (지수,일) 재실행 시 행수 불변(ON CONFLICT DO UPDATE — 값은 최신으로)
def test_store_rows_upsert_idempotent(monkeypatch):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE index_daily (index_code TEXT, trade_date TEXT, open REAL,"
        " high REAL, low REAL, close REAL, volume INTEGER,"
        " PRIMARY KEY (index_code, trade_date))")
    # 배치(PostgreSQL)는 %s 그대로 — in-memory sqlite 검증용으로 ? 로만 치환
    monkeypatch.setattr(ib, "UPSERT_SQL", ib.UPSERT_SQL.replace("%s", "?"))
    rows = [("KOSPI", "2026-11-19", 5090.04, 5137.89, 4963.61, 5137.89, 195695),
            ("KOSPI", "2026-11-20", 4928.51, 4971.31, 4831.69, 4869.96, 193704),
            ("KOSDAQ", "2026-11-20", 660.98, 665.15, 642.13, 645.68, 384002)]
    assert ib.store_rows(conn, rows) == 3   # 첫 적재 — 3행 신규
    n = conn.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0]
    assert n == 3
    # 재실행(값 변경 포함) — DO UPDATE: 행수 불변, 값은 최신으로 갱신
    assert ib.store_rows(conn, [
        ("KOSPI", "2026-11-20", 4900.0, 4975.0, 4830.0, 4870.0, 200000)]) == 1
    n = conn.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0]
    assert n == 3                           # 2회 실행 시 행수 불변(멱등 — 스펙 테스트 ②)
    row = conn.execute(
        "SELECT open, close, volume FROM index_daily"
        " WHERE index_code='KOSPI' AND trade_date='2026-11-20'").fetchone()
    assert row == (4900.0, 4870.0, 200000)  # DO UPDATE 로 기존 행 갱신(DO NOTHING 아님)
    conn.close()


# ③ 결측 행(빈 값)은 건너뛰고 카운트에 기록 — 유효 행만 rows 로, skipped 로 센다
def test_parse_response_skips_missing_and_counts():
    text = ("\n [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],\n"
            '["20261119", 5090.04, 5137.89, 4963.61, 5137.89, 195695, 0.0],\n'
            '["20261120", "", "", "", "", "", 0.0],\n'          # 전부 빈 값
            '["20261121", 4928.51, 4971.31, "", 4869.96, 193704, 0.0]]\n')  # 저가 결측
    raw_n, rows, skipped = ib.parse_response(text)
    assert raw_n == 3                       # 원천 행수는 결측 포함 전부(자기신고 3분리)
    assert skipped == 2                     # 결측 행 2건 — 건너뛰되 카운트에 기록(스펙 테스트 ③)
    assert rows == [("2026-11-19", 5090.04, 5137.89, 4963.61, 5137.89, 195695)]
