"""data_gap.cmd_backfill — '일을 안 한 실행'도 자기신고 하트비트를 남긴다 (R27 회귀).

왜 필요한가 (실측 2026-10-02 16:0x)
- R27 의 판정은 ``dq_runner_claim`` 의 14일 내 **행 존재**다(텍스트 휴리스틱 아님).
- 그런데 data_gap 백필 러너는 공실이 없으면 claim 행을 아예 남기지 않았다(코드상 claim_start 는
  백필이 실제로 시작될 때만 호출). 공실 0 은 **정상 상태**이므로 정상 시스템에서 R27 이 상시
  미달(check=1)로 오탐됐다 — 크론 로그에는 매 실행 "백필할 공실 없음"이 찍혀 있었다.
- 수리: 아무 일도 안 한 종료 경로(잠금·stale·공실없음·수집기실행중)에서도 source=0/claimed=0
  하트비트 1행을 남긴다. 이 행은 parse_failure(= source>0 AND claimed==0)를 만들지 않으므로
  DQ 지표를 오염시키지 않고, '돌았음'만 증명한다(크론이 죽으면 행이 사라져 R27 이 진짜로 운다).
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import data_gap as dg  # noqa: E402


class _ClaimSpy:
    def __init__(self, start_ok=True, raise_on_start=False):
        self.start_ok = start_ok
        self.raise_on_start = raise_on_start
        self.starts = []
        self.finishes = []

    def start(self, runner, table, note=""):
        if self.raise_on_start:
            raise RuntimeError("boom")
        self.starts.append((runner, table, note))
        return self.start_ok

    def finish(self, runner, claimed_rows=None, source_rows=None,
               persisted_rows=None, note=None):
        self.finishes.append(dict(runner=runner, claimed=claimed_rows,
                                  source=source_rows, persisted=persisted_rows))
        return True


def _wire(monkeypatch, spy, gaps=(), stale=(False, "")):
    monkeypatch.setattr(dg, "claim_start", spy.start)
    monkeypatch.setattr(dg, "claim_finish", spy.finish)
    monkeypatch.setattr(dg, "stale_warning", lambda: stale)
    monkeypatch.setattr(dg, "find_gaps", lambda probe=False: (list(gaps), set()))
    # 잠금 파일은 존재하지 않는 임시 경로로
    monkeypatch.setattr(dg, "LOCK_PATH", "/tmp/__dg_noop_test_absent.lock")
    if os.path.exists(dg.LOCK_PATH):
        os.remove(dg.LOCK_PATH)


def test_no_gap_run_records_zero_heartbeat(monkeypatch):
    """공실 0 = 정상. 이때도 러너가 '돌았음'을 남겨야 R27 이 정상 상태를 통과한다."""
    spy = _ClaimSpy()
    _wire(monkeypatch, spy, gaps=[])
    assert dg.cmd_backfill() == 0
    assert len(spy.finishes) == 1, "일을 안 한 실행이 자기신고를 안 남기면 R27 이 상시 미달이 된다"
    assert spy.finishes[0]["source"] == 0 and spy.finishes[0]["claimed"] == 0
    assert spy.starts and spy.starts[0][0] == "data_gap_backfill"


def test_lock_held_records_heartbeat(monkeypatch):
    spy = _ClaimSpy()
    _wire(monkeypatch, spy)
    monkeypatch.setattr(dg, "LOCK_PATH", __file__)  # 존재하는 파일 = 잠금 보유
    assert dg.cmd_backfill() == 0
    assert len(spy.finishes) == 1


def test_busy_collector_records_heartbeat_and_skips_child(monkeypatch):
    spy = _ClaimSpy()
    _wire(monkeypatch, spy, gaps=[("2026-09-30", 0)])
    seen = {"child_ran": False}

    def fake_run(cmd, **k):
        # pgrep 은 shell 문자열, 백필 자식은 리스트(cmd)로 온다 — 자식은 돌면 안 된다.
        if not isinstance(cmd, str):
            seen["child_ran"] = True
        return types.SimpleNamespace(stdout="12345 pgrep-x", returncode=0)

    monkeypatch.setattr(dg.subprocess, "run", fake_run)
    assert dg.cmd_backfill() == 0
    assert len(spy.finishes) == 1
    assert seen["child_ran"] is False, "수집기 실행 중인데 백필 자식을 띄웠다"


def test_real_backfill_path_keeps_delta_not_heartbeat(monkeypatch):
    """공실이 있고 수집기가 없으면 하트비트가 아니라 **델타** 자기신고 1건만 남는다."""
    spy = _ClaimSpy()
    _wire(monkeypatch, spy, gaps=[("2026-09-30", 0)])

    def fake_run(cmd, **k):
        if isinstance(cmd, str):
            return types.SimpleNamespace(stdout="", stderr="", returncode=0)  # pgrep: 비어 있음
        return types.SimpleNamespace(stdout="backfill ok", stderr="", returncode=0)
    monkeypatch.setattr(dg.subprocess, "run", fake_run)
    monkeypatch.setattr(dg, "pg_count", lambda d: 5)
    assert dg.cmd_backfill() == 0
    assert len(spy.finishes) == 1, "델타 신고 1건이어야 한다(하트비트 중복 금지)"
    assert spy.finishes[0]["source"] == 5 and spy.finishes[0]["claimed"] == 5


def test_claim_failure_does_not_break_backfill(monkeypatch):
    """자기신고가 예외를 올려도 수집 경로는 0 으로 끝난다(배선 원칙)."""
    spy = _ClaimSpy(raise_on_start=True)
    _wire(monkeypatch, spy, gaps=[])
    assert dg.cmd_backfill() == 0


def test_missing_wiring_is_safe(monkeypatch):
    """dq_claim 모듈이 없는 환경(점검 모드)에서도 죽지 않는다."""
    spy = _ClaimSpy()
    _wire(monkeypatch, spy, gaps=[])
    monkeypatch.setattr(dg, "claim_start", None)
    monkeypatch.setattr(dg, "claim_finish", None)
    assert dg.cmd_backfill() == 0
    assert spy.finishes == []
