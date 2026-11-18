"""KIS 서버 내부 처리 오류(OPSQ0008)는 **재시도**해야 한다.

실측 근거(2026-11-18): 19:10 수급 러너가 327종목 중 136종목(41.6%)에서
``OPSQ0008 호출 후처리(MCI전송) 오류`` (rt_cd=1, http=200)를 받았다. 같은 계열 4종목을
2시간 뒤 재프로브하니 3종목이 정상 수집됐다 → 일시적이다. 종전 분류는 이를 '비일시적'으로
보고 재시도 없이 종목을 버려, 그 종목의 수급이 다음 회전(≈3거래일)까지 동결됐다.

계약:
  · OPSQ0008 은 INTERNAL_RETRY_MAX 회까지 재시도한다(쿼터 보호를 위해 상한 고정).
  · 그 밖의 미분류 코드는 종전대로 즉시 실패(진짜 실패를 조용히 만들지 않는다).
  · 스키마 오류(OPSQ2001)는 여전히 즉시 실패.
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "kis-collector"))

from kis_app.client.kis_client import (  # noqa: E402
    INTERNAL_RETRY_MAX, KisApiError, KisClient,
)

BASE_URL = "https://openapi.koreainvestment.com:9443"


def _client(retry_max=3):
    return KisClient(
        "appkey", "appsecret", BASE_URL,
        retry_max=retry_max, retry_base_delay=0.0,
        sleep_fn=lambda s: None,
        curl_runner=lambda args, timeout=30: (200, "{}"),
    )


class _Seq:
    """호출마다 정해진 예외/값을 돌려주는 함수."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _opsq(msg_cd="OPSQ0008"):
    return KisApiError(msg_cd, "호출 후처리(MCI전송) 오류 입니다.", rt_cd="1", http_status=200)


def test_internal_error_recovers_after_retry():
    fn = _Seq(_opsq(), _opsq(), "ok")
    assert _client()._call_with_retry(fn) == "ok"
    assert fn.calls == 3


def test_internal_error_is_bounded():
    """상한을 넘으면 포기한다 — 고착 종목에서 쿼터를 태우지 않는다."""
    fn = _Seq(*([_opsq()] * 10))
    with pytest.raises(KisApiError):
        _client()._call_with_retry(fn)
    assert fn.calls == 1 + INTERNAL_RETRY_MAX


def test_unknown_code_still_fails_fast():
    fn = _Seq(_opsq("OPSQ0083"), "ok")
    with pytest.raises(KisApiError):
        _client()._call_with_retry(fn)
    assert fn.calls == 1


def test_schema_error_not_retried():
    fn = _Seq(KisApiError("OPSQ2001", "INPUT 필드 오류", rt_cd="1"), "ok")
    with pytest.raises(KisApiError):
        _client()._call_with_retry(fn)
    assert fn.calls == 1


def test_internal_flag_only_for_measured_code():
    assert KisApiError("OPSQ0008", "x").internal_transient is True
    assert KisApiError("OPSQ0083", "x").internal_transient is False
    assert KisApiError("EGW00123", "x").internal_transient is False
