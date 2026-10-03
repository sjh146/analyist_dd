"""researcher_cycle.start_item — 짧은 항목이 남기는 **고아 pidfile** 회귀 검증.

WHY (실측 2026-10-03): 리서처 tick 은 자체 Popen 경로라, ME 구동기(base.start_background)에
있는 '3초 기동 확인' 가드가 없었다. R28(pytest, 0.02초)처럼 즉시 끝나는 자식은 종료 시
**자기 pidfile 을 먼저 지우고**, 부모 tick 이 그 뒤에 pidfile 을 다시 쓰는 경합이 생긴다 →
죽은 pid 를 가리키는 고아 pidfile 이 남고, hygiene 이
'죽은 프로세스의 사이클 pidfile 잔존: researcher' breach 를 06:30~15:30(9시간) 보고했다
(16:01 새 틱이 pidfile 을 덮어써서야 해소).

경계를 양방향으로 고정한다:
  · 짧은 자식(즉시 종료) → pidfile·state 를 **지운다**(finished_early=True)
  · 살아있는 자식 → pidfile·state 를 **남긴다**(finished_early=False) — '실행 중' 신호 보존
"""
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def _wire(monkeypatch, tmp_path):
    rt = tmp_path / "res_cycle"
    rt.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rc, "RES_RUNTIME", str(rt))
    monkeypatch.setattr(rc.base, "PIDFILE", str(rt / "running.pid"))
    monkeypatch.setattr(rc.base, "STATE", str(rt / "state.json"))
    return rt / "running.pid", rt / "state.json"


def test_short_child_leaves_no_orphan_pidfile(monkeypatch, tmp_path):
    pidfile, state = _wire(monkeypatch, tmp_path)
    p, done = rc.start_item([sys.executable, "-c", "pass"], "R99", settle=0.6)
    p.wait()
    assert done is True
    assert not pidfile.exists(), "짧은 자식이 pidfile 을 남기면 hygiene 이 고아로 오보한다"
    assert not state.exists()


def test_live_child_keeps_pidfile(monkeypatch, tmp_path):
    pidfile, state = _wire(monkeypatch, tmp_path)
    p, done = rc.start_item([sys.executable, "-c", "import time; time.sleep(30)"], "R99",
                            settle=0.4)
    try:
        assert done is False
        assert pidfile.exists() and pidfile.read_text(encoding="utf-8").strip() == str(p.pid)
        assert json.loads(state.read_text(encoding="utf-8"))["pid"] == p.pid
    finally:
        p.terminate()
        p.wait()


def test_racing_child_orphan_is_reaped(monkeypatch, tmp_path):
    """자식이 '부모의 pidfile 쓰기'보다 먼저 죽는 경합을 결정론적으로 재현한다.

    실제 경합에서는 자식이 자기 pidfile 을 지운 뒤 부모가 다시 쓰므로, 부모가 pidfile 을 쓴
    시점엔 자식이 이미 죽어 있다. 여기서는 자식이 pidfile 을 **전혀 건드리지 않는** fake 로
    그 결과 상태를 만든다 → 가드가 지워야 한다(지우지 않으면 고아가 남는다).
    """
    pidfile, state = _wire(monkeypatch, tmp_path)

    class _DeadChild:
        pid = 987654

        def poll(self):          # 이미 종료됨
            return 0

    monkeypatch.setattr(rc.subprocess, "Popen", lambda cmd, **kw: _DeadChild())
    _, done = rc.start_item(["x"], "R99", settle=0)
    assert done is True
    assert not pidfile.exists()
    assert not state.exists()
