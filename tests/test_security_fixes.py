"""보안 수정 회귀 — 마스킹 우회, 미정의 변수, 하드코딩 기본값."""
from __future__ import annotations

import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_telemetry_masks_hyphenated_and_plain_accounts():
    tele = _load("tele", "services/feed_relay/client/telemetry.py")
    assert tele.mask("계좌 50123456-01 체결") == "계좌 ***-**** 체결"
    assert "5012345601" not in tele.mask("acct 5012345601 buy")
    assert "501234560123" not in tele.mask("계좌번호 501234560123 입금")
    assert tele.mask("2026-11-23 15:30 사이클") == "2026-11-23 15:30 사이클"


def test_internal_api_report_has_no_undefined_request_scope():
    text = (REPO / "services/api-gateway/app/internal_api.py").read_text(encoding="utf-8")
    assert "req.request_type" not in text
    assert '"request_type": "stock_report"' in text


def test_swing_performance_requires_password_from_env():
    text = (REPO / "scripts/swing_performance.py").read_text(encoding="utf-8")
    assert 'os.environ.get("POSTGRES_PASSWORD", "")' in text
    assert "9MrYGP4" not in text
