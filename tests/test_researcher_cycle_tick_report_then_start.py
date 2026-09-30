"""researcher_cycle.tick — 결과 보고가 '시작 기회'를 삼키지 않는지 회귀 검증.

WHY (실측 2026-09-30 22:0x): 종전 tick() 은 미보고 결과가 있으면 그것을 출력하고 **거기서
return** 했다. 그 결과 항목의 실행 간격은 '시작 틱 → 보고 틱 → 시작 틱' = 4시간이 되고,
pick_item 의 쿨다운(180분 = 1.5틱)은 그 사이에 항상 만료됐다 → 상시 감시 R21(prio 6,
recurring)이 매 시작 틱을 독식하고 R23(prio 7)은 영구 pending 이었다.

경계를 고정한다:
  · 미보고 결과가 있는 틱에서도 다음 pending 을 **시작한다**(보고와 시작은 배타가 아니다)
  · 보고 처리(reported=True + 원장 재기록)는 그대로 일어난다
  · 실행 가능한 pending 이 없으면 시작하지 않는다(무음 아님 — 안내 문구 유지)
"""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


class _FakePopen:
    pid = 424242


def _prep(monkeypatch, tmp_path, backlog_items, ledger_rows, picked):
    rt = tmp_path / "res_cycle"
    (rt / "logs").mkdir(parents=True, exist_ok=True)
    bl = tmp_path / "backlog.json"
    bl.write_text(json.dumps({"schema": 1, "items": backlog_items}, ensure_ascii=False),
                  encoding="utf-8")
    monkeypatch.setattr(rc, "RES_BACKLOG", str(bl))
    monkeypatch.setattr(rc, "RES_RUNTIME", str(rt))
    monkeypatch.setattr(rc, "RES_LOGDIR", str(rt / "logs"))
    monkeypatch.setattr(rc.base, "RUNTIME", str(rt))
    monkeypatch.setattr(rc.base, "PIDFILE", str(rt / "running.pid"))
    monkeypatch.setattr(rc.base, "STATE", str(rt / "state.json"))
    monkeypatch.setattr(rc.base, "running_pid", lambda *a, **k: None)
    monkeypatch.setattr(rc.base, "load_ledger", lambda *a, **k: list(ledger_rows))
    monkeypatch.setattr(rc.base, "_rewrite_ledger", lambda rows: None)
    monkeypatch.setattr(rc, "snapshot", lambda *a, **k: (0, ""))
    monkeypatch.setattr(rc, "north_star", lambda *a, **k: "")
    monkeypatch.setattr(rc, "snapshot_brief", lambda *a, **k: [])
    monkeypatch.setattr(rc.base, "guards", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(rc, "pick_item", lambda b, force=False: picked(b))
    started = []

    def _popen(cmd, **kw):
        started.append(cmd)
        return _FakePopen()

    monkeypatch.setattr(rc.subprocess, "Popen", _popen)
    return started


def _row(**kw):
    r = {"id": "R21", "title": "감시", "ts": rc.now_kst().isoformat(), "rc": 0, "verdict": "충족",
         "elapsed_min": 0.1, "detail": "d", "log": "l", "reported": False}
    r.update(kw)
    return r


def _item(iid="R23", prio=7):
    return {"id": iid, "title": iid, "priority": prio, "status": "pending",
            "command": "true"}


def test_tick_reports_and_still_starts_next_item(monkeypatch, tmp_path):
    """미보고 결과가 있는 틱에서도 다음 항목을 시작해야 한다(회전의 전제)."""
    started = _prep(monkeypatch, tmp_path, [_item()], [_row()], lambda b: b["items"][0])
    rc.tick()
    assert started, "결과 보고 틱이 시작 기회를 삼켰다 — pick_item 이 실행되지 않았다"


def test_tick_still_starts_when_nothing_to_report(monkeypatch, tmp_path):
    """미보고 결과가 없을 때의 종전 동작은 그대로(회귀 없음)."""
    started = _prep(monkeypatch, tmp_path, [_item()], [_row(reported=True)],
                    lambda b: b["items"][0])
    rc.tick()
    assert started


def test_tick_does_not_start_when_pick_returns_none(monkeypatch, tmp_path):
    """후보가 없으면 시작하지 않고 안내만 한다(조용한 성공으로 위장 금지)."""
    started = _prep(monkeypatch, tmp_path, [], [], lambda b: None)
    rc.tick()
    assert not started
