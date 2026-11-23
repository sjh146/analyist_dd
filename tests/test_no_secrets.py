"""시크릿 유출 회귀 — 추적 파일에 .env 값·개인키·에이전트 로그가 섞이지 않았는지 검사.

공개 저장소(analysist_dd)에서 실제로 DB 비밀번호가 에이전트 출력 파일(.oc)로 유출된 사고가 있었다.
이 테스트는 같은 유형을 커밋 전에 잡는다. .env 가 없는 환경(CI·새 머신)에서는 값 검사는 건너뛴다.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN_TRACKED = (".env",)
FORBIDDEN_SUFFIX = (".oc", ".stderr", ".pem", ".p12", ".pfx")
MIN_SECRET_LEN = 12
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|APP_KEY|PRIVATE_KEY|WEBHOOK|DSN)", re.I)


def _example_values() -> dict[str, str]:
    path = REPO / ".env.example"
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def _tracked() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                         capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def _env_values() -> dict[str, str]:
    path = REPO / ".env"
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip('"').strip("'")
        key = key.strip()
        if len(val) >= MIN_SECRET_LEN and SECRET_NAME.search(key):
            values[key] = val
    return values


def test_env_not_tracked():
    tracked = _tracked()
    assert ".env" not in tracked, ".env 가 추적되고 있다(즉시 untrack + 키 회전)"
    assert "trader-agent/.env" not in tracked


def test_agent_output_artifacts_not_tracked():
    bad = [p for p in _tracked() if p.endswith(FORBIDDEN_SUFFIX)]
    assert bad == [], "에이전트 출력/키 파일이 추적되고 있다: {0}".format(bad)


def test_no_env_values_in_tracked_files():
    values = _env_values()
    defaults = _example_values()
    values = {k: v for k, v in values.items() if defaults.get(k) != v}
    if not values:
        return
    leaks = []
    for key, val in values.items():
        out = subprocess.run(["git", "-C", str(REPO), "grep", "-l", "-F", "--", val],
                             capture_output=True, text=True)
        if out.stdout.strip():
            files = out.stdout.strip().splitlines()
            leaks.append((key, files[:3]))
    assert leaks == [], "추적 파일에 .env 값이 있다: {0}".format(leaks)


def test_no_private_key_material():
    marker = "BEGIN OPENSSH PRIVATE " + "KEY"
    out = subprocess.run(["git", "-C", str(REPO), "grep", "-l", "--", marker,
                          ":(exclude)tests/test_no_secrets.py"],
                         capture_output=True, text=True)
    assert out.stdout.strip() == "", "개인키가 추적되고 있다: " + out.stdout.strip()
