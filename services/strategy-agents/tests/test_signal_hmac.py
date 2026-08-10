import os
import hmac
import hashlib

os.environ["TRADE_SIGNAL_SECRET"] = "unit-test-secret"

from app.storage.redis_storage import _sign_signal, verify_signal_signature  # noqa: E402


def test_sign_and_verify_roundtrip():
    data = {"strategy_name": "theme_trading", "stock_code": "005930",
            "signal": "buy", "confidence": "0.9", "timestamp": "2026-08-10T00:00:00"}
    signed = _sign_signal(data)
    assert "sig" in signed
    # 원본 데이터는 그대로 보존
    for k, v in data.items():
        assert signed[k] == v
    # 서명 검증 통과
    assert verify_signal_signature(dict(signed)) is True


def test_tampered_signal_rejected():
    data = {"strategy_name": "theme_trading", "stock_code": "005930",
            "signal": "buy", "confidence": "0.9", "timestamp": "2026-08-10T00:00:00"}
    signed = _sign_signal(data)
    # 공격자가 수량/종목을 바꿈 → 서명 불일치 → 거부
    signed["stock_code"] = "000660"
    assert verify_signal_signature(dict(signed)) is False


def test_missing_signature_rejected():
    data = {"strategy_name": "theme_trading", "stock_code": "005930",
            "signal": "buy", "confidence": "0.9", "timestamp": "2026-08-10T00:00:00"}
    assert verify_signal_signature(dict(data)) is False


def test_no_secret_fails_closed():
    os.environ.pop("TRADE_SIGNAL_SECRET", None)
    try:
        data = {"strategy_name": "x", "stock_code": "y", "signal": "buy"}
        # fail-closed: 시크릿 미설정 시 모든 신호 거부 (CWE-306 기본 배포 no-op 차단)
        assert verify_signal_signature(dict(data)) is False
        assert "sig" not in _sign_signal(data)
    finally:
        os.environ["TRADE_SIGNAL_SECRET"] = "unit-test-secret"
