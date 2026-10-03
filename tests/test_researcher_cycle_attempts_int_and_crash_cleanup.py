"""researcher_cycle — ① `attempts` 가 정수인 항목에서 죽지 않는가 ② 크래시에도 pidfile 을 지우는가.

WHY (실측 2026-10-03 18:00 R28): 백로그 항목의 `attempts` 는 list 계약인데 **정수 `0`** 으로
저장돼 있었다. execute() 의 `it.setdefault("attempts", []).append(...)` 가 0 을 돌려받아
`AttributeError: 'int' object has no attribute 'append'` 로 죽었고, 그 줄이
`base.append_ledger(rec)` **뒤**라 ① 원장에는 결과가 남는데 백로그 status/result 는 갱신되지
않았으며 ② traceback 종료로 `--run` 경로의 pidfile 정리(finally 밖)도 스킵돼 고아 running.pid 가
남았다 — hygiene 이 '죽은 프로세스의 사이클 pidfile 잔존: researcher' breach 를 다음 점검까지
보고하는 경로(06:30~15:30 9시간 오보의 재발).

두 결함을 각각 고정한다: 계약 위반 데이터는 **교정하고 계속** 가고, 예외로 죽어도 **pidfile 은 지운다**.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def _item():
    return {"id": "R99", "title": "t", "status": "pending", "priority": 50,
            "kind": "collect", "command": "true", "attempts": 0,
            "check_target": {"op": ">=", "value": 0, "unit": "u"}}


def _backlog(tmp_path, item):
    p = tmp_path / "backlog.json"
    p.write_text(json.dumps({"items": [item]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _wire(monkeypatch, tmp_path, backlog_path):
    """execute() 의 무거운 부수효과(가드·Prometheus·자식 실행·원장·핸드오프)를 걷어낸다.

    ⚠ 원장 경로는 `rc.RES_LEDGER` 가 아니라 `rc.base.LEDGER` 로도 잡아야 한다 —
    실행 중에는 model_engineer_cycle 쪽 모듈 전역이 쓰인다(import 시 재바인딩된 사본).
    """
    monkeypatch.setattr(rc, "RES_BACKLOG", str(backlog_path))
    monkeypatch.setattr(rc, "RES_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(rc, "RES_LOGDIR", str(tmp_path / "logs"))
    monkeypatch.setattr(rc, "RES_RUNTIME", str(tmp_path / "rt"))
    monkeypatch.setattr(rc.base, "LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(rc.base, "LOGDIR", str(tmp_path / "logs"))
    monkeypatch.setattr(rc, "log", lambda *a, **k: None)
    monkeypatch.setattr(rc.base, "guards", lambda force=False: (True, ""))
    monkeypatch.setattr(rc, "snapshot", lambda n: (0, ""))
    monkeypatch.setattr(rc, "snapshot_brief", lambda out: [])
    monkeypatch.setattr(rc, "ensure_artifact", lambda item, run_log: (True, "", {}))
    monkeypatch.setattr(rc, "eval_check", lambda item: (0.0, "R99: 0 >= 0 → 충족", True))
    monkeypatch.setattr(rc, "handoff", lambda item, detail, verdict: None)


def test_int_attempts_is_coerced_and_execute_survives(monkeypatch, tmp_path):
    item = _item()
    bl = _backlog(tmp_path, item)
    _wire(monkeypatch, tmp_path, bl)
    assert rc.execute(dict(item)) == 0, "정수 attempts 하나로 틱이 죽으면 백로그 갱신·pidfile 정리가 통째로 스킵된다"
    saved = json.loads(bl.read_text(encoding="utf-8"))["items"][0]
    assert isinstance(saved["attempts"], list), "정수 attempts 는 list 로 교정돼야 한다"
    assert len(saved["attempts"]) == 1 and saved["attempts"][0]["rc"] == 0
    assert saved["result"] and saved["result"]["detail"], "결과가 백로그에 남지 않으면 다음 틱이 상태를 못 본다"


def test_list_attempts_still_appends(monkeypatch, tmp_path):
    item = _item()
    item["attempts"] = [{"ts": "old", "rc": 0, "detail": "이전"}]
    bl = _backlog(tmp_path, item)
    _wire(monkeypatch, tmp_path, bl)
    rc.execute(dict(item))
    saved = json.loads(bl.read_text(encoding="utf-8"))["items"][0]
    assert [a["ts"] for a in saved["attempts"]] == ["old", saved["attempts"][1]["ts"]]


def test_pidfile_removed_even_when_execute_raises(monkeypatch, tmp_path):
    bl = _backlog(tmp_path, _item())
    _wire(monkeypatch, tmp_path, bl)
    pidfile = tmp_path / "running.pid"
    pidfile.write_text("987654", encoding="utf-8")
    monkeypatch.setattr(rc.base, "PIDFILE", str(pidfile))

    def _boom(item, force=False):
        raise AttributeError("'int' object has no attribute 'append'")

    monkeypatch.setattr(rc, "execute", _boom)
    monkeypatch.setattr(sys, "argv", ["researcher_cycle.py", "--run", "R99"])
    with pytest.raises(AttributeError):
        rc.main()
    assert not pidfile.exists(), "예외로 죽을 때 pidfile 을 남기면 hygiene 이 고아 breach 로 오보한다"
