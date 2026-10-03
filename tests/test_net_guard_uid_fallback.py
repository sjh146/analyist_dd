"""net_guard 회귀 테스트 — uid 교차 상태 파일과 오류 중복 기록.

WHY (실측 사고, 2026-10-03): 크론(root)이 만든 0644 상태 파일을 사용자 프로세스가 열지 못해
`PermissionError` → **fail-open 으로 가드가 조용히 무력화**됐고(같은 오류를 9회 기록),
수동 실행 중에는 호출 간격 강제가 전혀 적용되지 않았다. 두 가지를 잠근다:
  ① 공유 파일을 못 열면 uid 별 파일로 폴백하고 **폴백 사실을 이벤트로 남긴다**(무력화 은폐 금지)
  ② 같은 가드 오류는 프로세스당 1회만 기록한다(로그·이벤트 오염 방지)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import net_guard as ng  # noqa: E402


@pytest.fixture()
def guard_env(tmp_path, monkeypatch):
    """임시 상태/이벤트 경로로 가드를 격리한다(운영 상태 오염 금지)."""
    monkeypatch.setenv("NET_GUARD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NET_GUARD_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("NET_GUARD_DISABLE", raising=False)
    monkeypatch.setattr(ng, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ng, "EVENTS", str(tmp_path / "events.jsonl"))
    # conftest 가 세션 전체에 NET_GUARD_DISABLE=1 을 걸어 둔다(운영 상태 오염 방지) →
    # 이 테스트는 '활성 가드' 를 검증하므로 모듈 플래그를 직접 되돌린다.
    monkeypatch.setattr(ng, "DISABLED", False)
    ng._FALLBACK_WARNED.clear()
    ng._GUARD_ERRS.clear()
    ng._CACHE.clear()
    os.makedirs(ng.STATE_DIR, exist_ok=True)
    return tmp_path


def _events(path):
    ev = path / "events.jsonl"
    if not ev.exists():
        return []
    return [l for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_permission_denied_falls_back_to_uid_file(guard_env, monkeypatch):
    """공유 상태 파일이 못 열리면 uid 별 파일로 내려가고, 폴백을 이벤트로 남긴다."""
    real_open = os.open
    calls = {"n": 0}

    def fake_open(path, flags, *a, **kw):
        if str(path).endswith(".lock") and ".u" not in str(path) and calls["n"] == 0:
            calls["n"] += 1
            raise PermissionError(13, "Permission denied")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(ng.os, "open", fake_open)
    # delay>0 이어야 가드가 활성(정책 없음 = no-op). 실무 기본은 호스트별 delay.
    g = ng.guard("unit-key", delay=0.2, jitter=0.0)
    assert g.acquire() >= 0.0                      # 예외 없이 진행(fail-open)
    ev = _events(guard_env)
    assert any('"guard_fallback"' in l for l in ev), ev
    assert any(".u" in l for l in ev), ev


def test_repeated_guard_errors_recorded_once(guard_env, monkeypatch):
    """같은 오류를 반복해도 이벤트는 1회만 남는다(실측 9회 중복 → 1회)."""
    monkeypatch.setattr(ng.Guard, "_acquire_locked",
                        lambda self, log=None: (_ for _ in ()).throw(RuntimeError("boom")))
    g = ng.guard("unit-key2", delay=0.2, jitter=0.0)
    for _ in range(5):
        assert g.acquire() == 0.0                  # fail-open
    errs = [l for l in _events(guard_env) if "guard_error" in l]
    assert len(errs) == 1, errs


def test_state_file_is_group_writable(guard_env):
    """상태 파일은 0666 으로 만든다 — 다음 uid(크론=root ↔ 수동=사용자)도 쓸 수 있어야 한다."""
    g = ng.guard("unit-key3", delay=0.2, jitter=0.0)
    g.acquire()
    _, path = ng._paths("unit-key3")
    mode = os.stat(path).st_mode & 0o777
    assert mode == 0o666, oct(mode)
