"""KRX 일봉 수집의 이름해석(DNS) 실패 재시도 — '도달하지 않은' 실패만 재시도한다.

WHY (실측 2026-11-09·11-13): 18:55 크론 시각에 WSL 이름해석이 간헐적으로 실패한다
(`[Errno -3] Temporary failure in name resolution` / curl exit=6). 종전 코드는 이걸 다른 전송
오류와 뭉뚱그려 **첫 실패에 중단**했고(`재시도하지 않고 중단 (IP 보호)`), 디스패처가 KIS 폴백
(종목당 1콜 ≈ 3.5시간)으로 넘어가 19:40 시장패턴이 **부분 적재된 시장**을 판정했다
(2026-11-13 19:41: scored=819 vs 전일 3828, trade_value 12.25조 vs 22.53조).

핵심 계약: DNS 실패만 재시도, 타임아웃·리셋은 종전대로 즉시 중단(요청이 도달했을 수 있으므로).
"""
import os
import socket
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import krx_daily  # noqa: E402


def _gaierror():
    return urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))


def _timeout():
    return urllib.error.URLError(socket.timeout("timed out"))


class _Resp:
    status = 200

    def __init__(self, body):
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _no_guard(monkeypatch):
    monkeypatch.setattr(krx_daily, "_guard", lambda: None)
    monkeypatch.setattr(krx_daily, "DNS_BACKOFF", 0.0)
    monkeypatch.setattr(krx_daily.time, "sleep", lambda s: None)


BODY = '{"OutBlock_1": [{"ISU_CD": "000020", "TDD_CLSPRC": "10000"}]}'


def test_is_dns_failure_true_for_gaierror():
    assert krx_daily._is_dns_failure(_gaierror()) is True


def test_is_dns_failure_true_for_raw_gaierror_message():
    assert krx_daily._is_dns_failure(Exception("[Errno -3] Temporary failure in name resolution")) is True


def test_is_dns_failure_false_for_timeout_and_reset():
    assert krx_daily._is_dns_failure(_timeout()) is False
    assert krx_daily._is_dns_failure(urllib.error.URLError(ConnectionResetError("reset"))) is False


def test_fetch_day_retries_dns_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _gaierror()
        return _Resp(BODY)

    monkeypatch.setattr(krx_daily.urllib.request, "urlopen", fake_urlopen)
    rows = krx_daily.fetch_day("https://x", "k", "path", "20261113")
    assert calls["n"] == 3
    assert rows and rows[0]["ISU_CD"] == "000020"


def test_fetch_day_gives_up_after_dns_retries(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        raise _gaierror()

    monkeypatch.setattr(krx_daily.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(krx_daily.KrxBlocked) as e:
        krx_daily.fetch_day("https://x", "k", "path", "20261113")
    assert calls["n"] == krx_daily.DNS_RETRIES + 1
    assert "이름해석" in str(e.value)


def test_fetch_day_does_not_retry_timeout(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        raise _timeout()

    monkeypatch.setattr(krx_daily.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(krx_daily.KrxBlocked) as e:
        krx_daily.fetch_day("https://x", "k", "path", "20261113")
    assert calls["n"] == 1, "타임아웃은 도달 가능성이 있어 재시도 금지(IP 보호)"
    assert "IP 보호" in str(e.value)


def test_fetch_day_http_429_still_hard_block(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        raise urllib.error.HTTPError("https://x", 429, "Too Many", {}, None)

    monkeypatch.setattr(krx_daily.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(krx_daily.KrxBlocked):
        krx_daily.fetch_day("https://x", "k", "path", "20261113")
    assert calls["n"] == 1, "HTTP 429 는 재시도 금지"
