"""docker 데몬 부재 시 위생 판정이 '거짓 수치'를 만들지 않는지(2026-11-16 실측 회귀).

실측 사고: 호스트 재기동 직후 dockerd 가 죽어 있는 동안 hygiene 이
containers_running=30 · unhealthy=["failed","to","connect",...] · dangling_volumes=30 을 기록했다
(오류 문장을 split 해 단어를 셈). 없는 사실이 보고되는 것을 막는다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import system_hygiene as sh_mod  # noqa: E402

ERR = ("failed to connect to the docker API at unix:///var/run/docker.sock; check if the path is "
       "correct and if the daemon is running: dial unix /var/run/docker.sock: connect: no such file or directory")


def test_docker_unreachable_detects_socket_error():
    assert sh_mod.docker_unreachable(ERR) is True
    assert sh_mod.docker_unreachable("__ERR__ timeout") is True
    assert sh_mod.docker_unreachable("Cannot connect to the Docker daemon at unix:///var/run/docker.sock") is True
    assert sh_mod.docker_unreachable("62f10d62fa09\nabcdef123456\n") is False


def test_docker_ids_counts_only_ids():
    assert sh_mod.docker_ids(ERR) == []
    assert sh_mod.docker_ids("62f10d62fa09\n") == ["62f10d62fa09"]
    assert len(sh_mod.docker_ids("62f10d62fa09\n7f2a4bfb3c71\n")) == 2


def test_docker_names_does_not_eat_error_words():
    assert sh_mod.docker_names(ERR) == []
    assert sh_mod.docker_names("stock_postgres\nstock_redis\n") == ["stock_postgres", "stock_redis"]


def test_check_docker_down_marks_undecidable(monkeypatch):
    monkeypatch.setattr(sh_mod, "sh", lambda *a, **k: ERR)
    info = sh_mod.check_docker()
    assert info.get("docker_down")
    assert info["containers_running"] is None
    assert info["containers_stopped"] is None
    assert info["unhealthy"] == []
    assert info["dangling_volumes"] == 0


def test_check_docker_healthy_counts(monkeypatch):
    def fake(cmd, timeout=60):
        if cmd.startswith("docker ps -aq"):
            return "62f10d62fa09\n7f2a4bfb3c71\n"
        if cmd.startswith("docker ps -q"):
            return "62f10d62fa09\n"
        if cmd.startswith("docker ps --format"):
            return "stock_postgres\n"
        if cmd.startswith("docker inspect"):
            return "0"
        if cmd.startswith("docker system df"):
            return "Images|3|172MB|61MB (35%)\nBuild Cache|37|12.39GB|12.39GB"
        return ""
    monkeypatch.setattr(sh_mod, "sh", fake)
    monkeypatch.setattr(sh_mod, "_prev_restart_counts", lambda: {})
    info = sh_mod.check_docker()
    assert not info.get("docker_down")
    assert info["containers_running"] == 1
    assert info["containers_stopped"] == 1
    assert info["unhealthy"] == []
