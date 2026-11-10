"""researcher_cycle.tick — '기록 없이 죽은' 실행을 조용히 지나가지 않는지 회귀 검증.

WHY (실측 2026-11-09/10): 4주 호스트 오프라인 복구 중 R33 백필 자식이 호스트 종료로
SIGKILL 됐다(11-09 22:56 215/798 · 11-10 00:28 135/585). pidfile·state 는 남았지만
tick 은 `running_pid()` 가 None 이면 아무 표시 없이 다음 항목을 시작해, 원장에 그 실행
기록이 0건이 됐다(원장만 보면 'R33 은 안 돌았다'로 읽힌다). 진행분은 종목별 커밋이라
보존됐다(완료 353 = 215 + 135 + 3).

경계:
  · pidfile/state 가 **죽은 pid** 를 가리키면 tick 이 '기록 없이 죽었다' 줄을 찍고 파일을 지운다
  · **살아있는** 사이클 pid(*_cycle.py)면 아무것도 지우지 않는다
  · 파일이 없으면 침묵한다(정상 종료 경로 — 자식이 자기 파일을 지운다)
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


class _FakePopen:
    pid = 424242

    def poll(self):
        return None


class _SubprocessShim:
    """rc.subprocess 만 가짜로 바꾼다 — 진짜 subprocess 모듈을 monkeypatch 하면
    이 테스트 자신의 Popen 까지 오염된다(실측: proc.kill AttributeError)."""
    Popen = staticmethod(lambda *a, **k: _FakePopen())


def _prep(monkeypatch, tmp_path, running=False):
    rt = tmp_path / "res_cycle"
    (rt / "logs").mkdir(parents=True, exist_ok=True)
    bl = tmp_path / "backlog.json"
    bl.write_text(json.dumps({"schema": 1, "items": []}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rc, "RES_BACKLOG", str(bl))
    monkeypatch.setattr(rc, "RES_RUNTIME", str(rt))
    monkeypatch.setattr(rc, "RES_LOGDIR", str(rt / "logs"))
    monkeypatch.setattr(rc.base, "RUNTIME", str(rt))
    monkeypatch.setattr(rc.base, "PIDFILE", str(rt / "running.pid"))
    monkeypatch.setattr(rc.base, "STATE", str(rt / "state.json"))
    monkeypatch.setattr(rc.base, "running_pid", lambda *a, **k: None)
    monkeypatch.setattr(rc.base, "load_ledger", lambda *a, **k: [])
    monkeypatch.setattr(rc.base, "_rewrite_ledger", lambda rows: None)
    monkeypatch.setattr(rc, "snapshot", lambda *a, **k: (0, ""))
    monkeypatch.setattr(rc, "north_star", lambda *a, **k: "")
    monkeypatch.setattr(rc, "snapshot_brief", lambda *a, **k: [])
    monkeypatch.setattr(rc.base, "guards", lambda *a, **k: (False, "테스트: 시작 안 함"))
    monkeypatch.setattr(rc, "ORPHAN_SETTLE", 0)
    monkeypatch.setattr(rc, "subprocess", _SubprocessShim)
    return rt


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_dead_run_is_reported_and_cleaned(monkeypatch, tmp_path, capsys):
    rt = _prep(monkeypatch, tmp_path)
    (rt / "running.pid").write_text(str(_dead_pid()), encoding="utf-8")
    (rt / "state.json").write_text(json.dumps(
        {"id": "R33", "pid": _dead_pid(), "started": "2026-11-10T00:00:23+09:00"}),
        encoding="utf-8")
    rc.tick()
    out = capsys.readouterr().out
    assert "기록 없이 죽었다" in out and "R33" in out, out
    assert not (rt / "running.pid").exists() and not (rt / "state.json").exists()


def test_live_cycle_pid_is_not_reaped(monkeypatch, tmp_path, capsys):
    rt = _prep(monkeypatch, tmp_path)
    script = tmp_path / "zz_cycle.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)])
    try:
        time.sleep(0.3)
        (rt / "running.pid").write_text(str(proc.pid), encoding="utf-8")
        (rt / "state.json").write_text(json.dumps(
            {"id": "R33", "pid": proc.pid, "started": "?"}), encoding="utf-8")
        rc.tick()
        out = capsys.readouterr().out
        assert "기록 없이 죽었다" not in out, out
        assert (rt / "running.pid").exists()
    finally:
        proc.kill()
        proc.wait()


def test_no_files_silent(monkeypatch, tmp_path, capsys):
    _prep(monkeypatch, tmp_path)
    rc.tick()
    assert "기록 없이 죽었다" not in capsys.readouterr().out
