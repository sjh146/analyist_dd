"""KIS 오류 응답 파싱 테스트.

토큰 발급 실패는 ``error_code``/``error_description`` 형식으로 오는데, 클라이언트가
``msg_cd``/``msg1``만 읽던 시절에는 전부 ``EGW-UNKNOWN``으로 뭉개져
EGW00103(앱키 거부)과 EGW00133(분당 1회 제한)을 구분할 수 없었고 재시도도 걸리지 않았다.
"""
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "kis-collector"))

from kis_app.client.kis_client import (KisApiError, TokenManager,
                                       extract_kis_error)

BASE_URL = "https://openapi.koreainvestment.com:9443"


class FakeRunner:
    def __init__(self, responses):
        self._responses = list(responses)

    def __call__(self, args, timeout=30):
        return self._responses.pop(0)


def test_extract_quote_style():
    code, msg, rt_cd = extract_kis_error(
        {"msg_cd": "OPSQ2001", "msg1": "INPUT 필드 오류", "rt_cd": "1"})
    assert (code, msg, rt_cd) == ("OPSQ2001", "INPUT 필드 오류", "1")


def test_extract_gateway_style():
    code, msg, rt_cd = extract_kis_error(
        {"error_code": "EGW00103", "error_description": "유효하지 않은 AppKey입니다."})
    assert code == "EGW00103"
    assert "AppKey" in msg
    assert rt_cd == "1"


def test_extract_unknown_and_non_dict():
    assert extract_kis_error({})[0] == "EGW-UNKNOWN"
    assert extract_kis_error(None)[0] == "EGW-UNKNOWN"


def test_token_credential_error_surfaces_code():
    body = json.dumps({"error_code": "EGW00103",
                       "error_description": "유효하지 않은 AppKey입니다."})
    tm = TokenManager("k", "s", BASE_URL, token_path=None,
                      max_retries=0, curl_runner=FakeRunner([(403, body)]))
    with pytest.raises(KisApiError) as ei:
        tm.get_token()
    assert ei.value.msg_cd == "EGW00103"
    assert ei.value.credential_error is True


def test_token_rate_limit_via_error_code_retries():
    limited = json.dumps({"error_code": "EGW00133",
                          "error_description": "초당 거래건수를 초과하였습니다."})
    ok = json.dumps({"access_token": "tok-1", "expires_in": 86400})
    sleeps = []
    tm = TokenManager("k", "s", BASE_URL, token_path=None, max_retries=2,
                      rate_limit_sleep=60.0, sleep_fn=sleeps.append,
                      curl_runner=FakeRunner([(403, limited), (200, ok)]))
    assert tm.get_token() == "tok-1"
    assert sleeps == [60.0]   # error_code 형식이어도 제한 대기가 걸려야 한다
