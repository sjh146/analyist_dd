"""get_stock_by_name 종목명 매칭 정규화 테스트 (스펙 docs/spec_news_coverage_fix.md §1·§3).

WHY (2026-11-19 실측): news_events 12,828행 중 6,854행(53.4%)이 stock_code IS NULL —
원인 중 하나가 종목명 표기 차이(공백·(주)·㈜·대소문자)에 의한 정확일치 실패.
이 테스트는 새 캐스케이드(①원문 ②정규화 ③별칭 ④유니크 부분일치)와 실패 원장
(news_stock_match_failures upsert)의 계약을 검증한다.

실측 근거 (2026-11-19, 실제 DB 조회 — 기대값 하드코딩의 근거):

    docker exec stock_postgres psql -U stock_user -d stock_trading \
      -c "SELECT stock_code, stock_name FROM stocks WHERE stock_name IN ('삼성전자','SK하이닉스','NAVER')"
    → 삼성전자=005930, SK하이닉스=000660, NAVER=035420
    -c "SELECT count(*) FROM stocks WHERE upper(replace(replace(replace(replace(stock_name,' ',''),'(주)',''),'㈜',''),'주식회사','')) LIKE '%하이트진로2%'"
    → 1행: 하이트진로2우B(000087) — 정규화 부분일치 유니크 케이스
    -c "SELECT count(*) FROM stocks WHERE stock_name LIKE '%채권혼합%'"
    → 41행 (정확일치·정규화 equality 는 0행) — 2건 이상 모호 실패 케이스
    -c "SELECT count(*) FROM stocks WHERE upper(stock_name) LIKE '%존재하지않는종목%'"
    → 0행 — no_match 케이스

주의: 테스트는 실제 DB 를 사용한다(컨테이너 stock_news_analyzer → postgres).
실패 원장에 남기는 테스트 행은 각 테스트에서 삭제한다(재실행 안전).
"""
import pytest

from app.storage.postgres_storage import PostgresStorage, normalize_stock_name


@pytest.fixture(scope="module")
def storage():
    return PostgresStorage()


def _failure_row(storage, name_raw):
    """실패 원장 행 조회: (hits, last_reason, name_norm) 또는 None."""
    conn = storage._get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT hits, last_reason, name_norm FROM news_stock_match_failures "
            "WHERE name_raw = %s",
            (name_raw,),
        )
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        storage._put_conn(conn)


def _delete_failure(storage, name_raw):
    """테스트가 만든 실패 원장 행 삭제 (재실행 멱등)."""
    conn = storage._get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM news_stock_match_failures WHERE name_raw = %s",
            (name_raw,),
        )
        conn.commit()
        cur.close()
    finally:
        storage._put_conn(conn)


def test_normalized_name_match(storage):
    """② 정규화 equality: 접미사·공백·대소문자 차이가 매칭 성공으로 이어진다."""
    # '삼성전자(주)' → 정규화 '삼성전자' → 005930
    m = storage.get_stock_by_name("삼성전자(주)")
    assert m is not None
    assert m["stock_code"] == "005930"
    assert m["stock_name"] == "삼성전자"
    # 'SK 하이닉스' → 'SK하이닉스' → 000660
    m2 = storage.get_stock_by_name("SK 하이닉스")
    assert m2 is not None
    assert m2["stock_code"] == "000660"
    # 'naver' → 대문자 정규화 'NAVER' → 035420
    m3 = storage.get_stock_by_name("naver")
    assert m3 is not None
    assert m3["stock_code"] == "035420"
    # ① 원문 정확일치도 유지
    m4 = storage.get_stock_by_name("삼성전자")
    assert m4 is not None
    assert m4["stock_code"] == "005930"


def test_unique_partial_match(storage):
    """④ 정규화 부분일치: 히트가 정확히 1건이면 성공.

    '하이트진로2' 와 equality 인 종목은 없고, 정규화 이름에 이를 포함하는 종목은
    '하이트진로2우B'(000087) 1건뿐이다(실측 쿼리는 파일 docstring 참고).
    """
    m = storage.get_stock_by_name("하이트진로2")
    assert m is not None
    assert m["stock_code"] == "000087"
    assert m["stock_name"] == "하이트진로2우B"


def test_ambiguous_partial_fails(storage):
    """④ 2건 이상 부분일치 → 실패(None) + 원장에 ambiguous 기록.

    '채권혼합': 정확일치 0 · 정규화 equality 0 · 부분일치 41건(실측) →
    오매핑 금지 원칙에 따라 None.
    """
    name = "채권혼합"
    try:
        assert storage.get_stock_by_name(name) is None
        row = _failure_row(storage, name)
        assert row is not None
        assert row[1] == "ambiguous"  # last_reason
        assert row[2] == normalize_stock_name(name)
    finally:
        _delete_failure(storage, name)


def test_failure_record_idempotent(storage):
    """실패 원장 upsert 멱등: 같은 이름 2회 실패 → 1행, hits=2 (스펙 §3-⑤)."""
    name = "존재하지않는종목_zzq9f3a"
    try:
        assert storage.get_stock_by_name(name) is None
        assert storage.get_stock_by_name(name) is None
        row = _failure_row(storage, name)
        assert row is not None
        assert row[0] == 2  # hits: 2회 실패 → 1행 유지 + 횟수 증가
        assert row[1] == "no_match"
        assert row[2] == normalize_stock_name(name)
    finally:
        _delete_failure(storage, name)
