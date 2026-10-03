"""DART 차단 판정 회귀 테스트 — 문자열 검색 오탐 재발 방지.

WHY (실측 사고, 2026-10-03 18:10): `_blocked_looking` 이 정상 응답 dict 를 `str(data)` 로
문자열 검색해 "403" 이 있으면 차단으로 판정했다. DART 응답에는 종목코드(corp_code)·접수번호
(rcept_no) 같은 **긴 숫자열**이 들어 있어 '403' 이 우연히 포함될 수 있다(예: corp_code 0040304).
그날 18:10 적재는 **콜 1 / 삽입 0행**으로 끝났고 900초 쿨다운이 전 프로세스에 걸렸다.

교훈: 차단/한도 판정은 **구조화된 신호**(status 코드·HTTP 상태)로만 한다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import dart_disclosure_backfill as dbf  # noqa: E402


def _resp(status="000", **kw):
    base = {"status": status, "message": "정상", "page_no": 1, "page_count": 100,
            "total_count": 131, "total_page": 2, "list": []}
    base.update(kw)
    return base


def test_normal_response_is_not_block():
    """정상(status=000)은 차단이 아니다 — 사고의 직접 재현."""
    assert dbf._blocked_looking(_resp()) is False


def test_payload_digits_containing_403_do_not_block():
    """종목코드/접수번호 숫자에 '403' 이 들어가도 차단이 아니다(오탐의 실제 원인)."""
    data = _resp(list=[
        {"corp_code": "0040304", "corp_name": "삼성", "rcept_no": "20261003004031"},
        {"corp_code": "00140320", "corp_name": "테스트", "rcept_no": "40300000000000"},
    ])
    assert "403" in str(data)                      # 문자열에는 '403' 이 존재한다
    assert dbf._blocked_looking(data) is False      # 그럼에도 차단이 아니어야 한다


def test_no_data_status_is_not_block():
    """013(조회 데이터 없음)은 정상 — 빈 창에서 뜬다."""
    assert dbf._blocked_looking(_resp(status="013", list=[])) is False


def test_rate_limit_status_blocks():
    """020(요청 제한 초과)만 쿨다운 대상이다."""
    assert dbf._blocked_looking(_resp(status="020", message="요청 제한 초과")) is True


def test_key_error_status_blocks():
    """인증키 오류(100/101/102)는 중단 신호다."""
    for st in ("100", "101", "102"):
        assert dbf._blocked_looking(_resp(status=st)) is True


def test_http_403_exception_blocks():
    assert dbf._blocked_looking(RuntimeError("HTTP 403 Forbidden")) is True
    assert dbf._blocked_looking(RuntimeError(
        "403 Client Error: Forbidden for url: https://opendart.fss.or.kr/api/list.json")) is True


def test_html_waf_page_blocks():
    assert dbf._blocked_looking(RuntimeError("<html><body>Access Denied</body></html>")) is True


def test_transient_error_is_not_block():
    """타임아웃·5xx 는 재시도 대상이지 차단이 아니다."""
    assert dbf._blocked_looking(RuntimeError("HTTPSConnectionPool: Read timed out")) is False
    assert dbf._blocked_looking(RuntimeError("500 Server Error")) is False
    assert dbf._transient(RuntimeError("HTTP 503 Service Unavailable")) is True
    assert dbf._transient(RuntimeError("Read timed out")) is True
