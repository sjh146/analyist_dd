"""researcher_cycle 의 호스트 DB 좌표 정규화 회귀 테스트.

WHY (실측 2026-09-28 06:00 틱): 저장소 `.env` 는 **컨테이너용** 값(`POSTGRES_HOST=postgres`,
`POSTGRES_PORT=5432`)이다. 크론 틱 환경에는 POSTGRES_* 가 없고, 백로그 명령이 `.env` 를 스스로
읽는 스크립트(`scripts/r16_window_coverage.py::env_from_dotenv`)는 컨테이너 서비스명을 물려받아
호스트에서 이름해석에 실패했다 → R16 이 rc=1 '실패'로 원장에 기록(명령 자체는 정상이었다).

경계를 두 방향으로 고정한다:
  · 컨테이너 좌표(빈 값/postgres/localhost/0.0.0.0/5432)는 호스트 매핑(127.0.0.1:5434)으로 바꾼다
  · 운영자가 명시한 **다른** 좌표(원격 호스트·비표준 포트)는 건드리지 않는다(추측으로 덮으면
    원격 DB 를 쓰는 명령이 조용히 로컬 DB 를 읽는다 — 침묵 오염이 최악이다)
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def _env(monkeypatch, **kv):
    """POSTGRES_* 만 남긴 최소 환경에서 정규화 함수를 돌린다."""
    for k in list(kv):
        monkeypatch.setenv(k, kv[k])
    return rc._host_db_env()


def test_unset_becomes_host_mapping(monkeypatch):
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    e = rc._host_db_env()
    assert e["POSTGRES_HOST"] == "127.0.0.1"
    assert e["POSTGRES_PORT"] == "5434"


def test_container_service_name_is_mapped(monkeypatch):
    # 실측 실패 시나리오: .env 의 값 그대로
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    e = rc._host_db_env()
    assert e["POSTGRES_HOST"] == "127.0.0.1"
    assert e["POSTGRES_PORT"] == "5434"


def test_localhost_and_wildcard_mapped(monkeypatch):
    for host in ("localhost", "0.0.0.0"):
        monkeypatch.setenv("POSTGRES_HOST", host)
        assert rc._host_db_env()["POSTGRES_HOST"] == "127.0.0.1"


def test_operator_override_is_respected(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6000")
    e = rc._host_db_env()
    assert e["POSTGRES_HOST"] == "db.internal"
    assert e["POSTGRES_PORT"] == "6000"


def test_parent_environ_not_mutated(monkeypatch):
    """자식 환경만 바꾼다 — 사이클 자신의 os.environ 을 갈아치우면 다른 판정이 조용히 바뀐다."""
    import os
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    before = os.environ["POSTGRES_HOST"]
    rc._host_db_env()
    assert os.environ["POSTGRES_HOST"] == before


# ── 자격증명 폴백 (실측 2026-09-28 15:35 R11: 'no password supplied') ──────────
def test_credentials_filled_from_dotenv_when_loopback(monkeypatch):
    """호스트 좌표로 확정되면 비어 있는 자격증명을 .env 에서 채운다.

    실측: `scripts/build_macro_features.py` 는 기본값이 `password=""` 라, 좌표만 맞추고
    비밀번호를 안 물려주면 rc=1 `fe_sendauth: no password supplied` 로 죽는다.
    """
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setattr(rc, "_dotenv_get",
                        lambda k: {"POSTGRES_PASSWORD": "pw-from-env",
                                   "POSTGRES_USER": "u-from-env"}.get(k, ""))
    e = rc._host_db_env()
    assert e["POSTGRES_PASSWORD"] == "pw-from-env"
    assert e["POSTGRES_USER"] == "u-from-env"


def test_credentials_not_filled_for_remote_host(monkeypatch):
    """원격 좌표(운영자 명시)에는 로컬 자격증명을 주입하지 않는다."""
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setattr(rc, "_dotenv_get", lambda k: "pw-from-env")
    assert rc._host_db_env().get("POSTGRES_PASSWORD", "") == ""


def test_operator_password_is_not_overwritten(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PASSWORD", "explicit")
    monkeypatch.setattr(rc, "_dotenv_get", lambda k: "from-file")
    assert rc._host_db_env()["POSTGRES_PASSWORD"] == "explicit"


def test_dotenv_get_reads_repo_value():
    """.env 폴백이 실제 저장소에서 동작하는지(값이 있으면 비어 있지 않음)."""
    val = rc._dotenv_get("POSTGRES_PASSWORD")
    assert isinstance(val, str)
    assert val != "" and " " not in val.strip()
