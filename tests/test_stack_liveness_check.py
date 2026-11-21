"""stack_liveness_check 회귀 테스트.

WHY: 이 check 의 목적은 '컨테이너가 죽었는데 DQ 는 rc=0/3(다른 사유) 로 조용한' 상태를 결함 수로
드러내는 것이다(실측 2026-11-21: 앱 컨테이너 14개 소실). 따라서 세 계약을 고정한다.
  · 빠진 것을 정확히 세고, 정상일 때 0 이 된다(실패할 수 없는 check 금지).
  · docker 를 못 쓰면 0(=정상) 이 아니라 999(판정 불가=실패) 로 시끄럽게 남는다.
  · 핵심 목록에 jenkins 를 넣지 않는다(상시 좀비로 잡음이 되는 대상).
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stack_liveness_check",
                                              ROOT / "scripts" / "stack_liveness_check.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_all_running_is_zero_defect():
    assert mod.missing_services(set(mod.CORE_SERVICES)) == []


def test_missing_are_counted_and_sorted():
    running = set(mod.CORE_SERVICES) - {"prometheus", "api-gateway", "news-analyzer"}
    assert mod.missing_services(running) == ["api-gateway", "news-analyzer", "prometheus"]


def test_extra_services_do_not_count_as_defect():
    running = set(mod.CORE_SERVICES) | {"jenkins", "some-other"}
    assert mod.missing_services(running) == []


def test_jenkins_not_in_core():
    assert "jenkins" not in mod.CORE_SERVICES


def test_parse_services_ignores_blank_lines():
    text = "postgres\n\nprometheus\nredis\n"
    assert mod.parse_services(text) == {"postgres", "prometheus", "redis"}


def test_main_reports_999_when_docker_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(mod, "running_services", lambda proj=None: None)
    mod.main([])
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == str(mod.UNAVAILABLE), "판정 불가는 0(정상)이 아니라 999(실패)여야 한다"


def test_main_prints_defect_count_last(monkeypatch, capsys):
    running = set(mod.CORE_SERVICES) - {"api-gateway", "strategy-agents"}
    monkeypatch.setattr(mod, "running_services", lambda proj=None: running)
    mod.main([])
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].strip().endswith("= 2")
