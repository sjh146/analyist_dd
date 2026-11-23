"""테스트 러너(run_test_suites.py) 회귀 — .env 자체 로드 + 플래키 재시도.

WHY (2026-11-23 실측): 러너가 .env 를 읽지 않아 수동 실행 시 DB 비밀번호 누락으로
같은 코드가 4건/5건 실패로 다르게 보고됐다. 또 실패가 간헐적인지 확인할 경로가 없었다.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "run_test_suites_under_test", REPO / "scripts" / "run_test_suites.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_load_env_reads_file_and_forces_host_port(tmp_path, monkeypatch):
    mod = _load()
    env_file = tmp_path / ".env"
    env_file.write_text('POSTGRES_PASSWORD=s3cret\nPOSTGRES_HOST=postgres\nPOSTGRES_PORT=5432\n',
                        encoding="utf-8")
    monkeypatch.setattr(mod, "PROJ", str(tmp_path))
    for key in ("POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT"):
        monkeypatch.delenv(key, raising=False)

    mod.load_env()

    assert os.environ["POSTGRES_PASSWORD"] == "s3cret"
    assert os.environ["POSTGRES_HOST"] == "127.0.0.1"
    assert os.environ["POSTGRES_PORT"] == "5434"


def test_load_env_keeps_existing_values(tmp_path, monkeypatch):
    mod = _load()
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=fromfile\n", encoding="utf-8")
    monkeypatch.setattr(mod, "PROJ", str(tmp_path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "fromshell")
    mod.load_env()
    assert os.environ["POSTGRES_PASSWORD"] == "fromshell"


def test_load_env_missing_file_is_silent(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "PROJ", str(tmp_path))
    mod.load_env()
    assert os.environ["POSTGRES_HOST"] == "127.0.0.1"


def test_retry_marks_flaky_when_second_attempt_passes(monkeypatch):
    mod = _load()
    calls = []

    def fake(suite):
        calls.append(1)
        if len(calls) == 1:
            return {"name": suite["name"], "rc": 1, "failed": 2}
        return {"name": suite["name"], "rc": 0, "failed": 0}

    monkeypatch.setattr(mod, "run_suite", fake)
    result = mod.run_with_retries({"name": "x"}, 2)
    assert result["rc"] == 0 and result["flaky"] is True
    assert result["attempts"] == 2


def test_retry_keeps_failure_when_all_attempts_fail(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "run_suite",
                        lambda suite: {"name": suite["name"], "rc": 1, "failed": 1})
    result = mod.run_with_retries({"name": "x"}, 2)
    assert result["rc"] == 1 and result["flaky"] is False
    assert result["attempts"] == 3


def test_passing_suite_is_not_retried(monkeypatch):
    mod = _load()
    calls = []

    def fake(suite):
        calls.append(1)
        return {"name": suite["name"], "rc": 0, "failed": 0}

    monkeypatch.setattr(mod, "run_suite", fake)
    result = mod.run_with_retries({"name": "x"}, 2)
    assert result["attempts"] == 1 and len(calls) == 1
