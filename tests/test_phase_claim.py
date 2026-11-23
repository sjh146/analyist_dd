"""저녁 파이프라인 phase 자기신고(R28) 테스트.

2026-11-23: 컨테이너 phase(뉴스·경제일정·재무·벡터)는 dq_claim 배선을 쓸 수 없어
수집 실패가 `dq_runner_claim` 에 흔적을 남기지 않았다. 호스트가 phase 전/후 테이블
count 를 재서 델타를 자기신고한다. 여기서는 계산·규약(지어내지 않기, exit 0)을 고정한다.
"""
from __future__ import annotations

import io
import pathlib
import sys
from contextlib import redirect_stderr

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import phase_claim  # noqa: E402


def test_delta_is_after_minus_before() -> None:
    assert phase_claim.compute_delta("100", "130") == 30
    assert phase_claim.compute_delta(100, 100) == 0
    assert phase_claim.compute_delta(130, 100) == -30


def test_unknown_counts_are_not_invented() -> None:
    assert phase_claim.compute_delta("", "130") is None
    assert phase_claim.compute_delta("abc", "130") is None
    assert phase_claim.compute_delta(None, None) is None


def test_note_carries_rc_and_counts() -> None:
    note = phase_claim.build_note(0, "5", "9")
    assert "rc=0" in note and "5->9" in note


def test_record_skips_when_counts_unknown() -> None:
    err = io.StringIO()
    with redirect_stderr(err):
        ok = phase_claim.record("pipeline_news", "news_analysis", "", "10", rc=0)
    assert ok is False
    assert "자기신고 생략" in err.getvalue()


def test_record_persists_delta(monkeypatch) -> None:
    captured = {}

    class _Conn:
        def close(self):
            captured["closed"] = True

    class _Claim:
        @staticmethod
        def _open_conn():
            return _Conn()

        @staticmethod
        def record_claim(conn, runner, table, claimed_rows, persisted_rows=None,
                         source_rows=None, note=None):
            captured.update(runner=runner, table=table, claimed=claimed_rows,
                            persisted=persisted_rows, source=source_rows, note=note)

    monkeypatch.setitem(sys.modules, "dq_claim", _Claim)
    ok = phase_claim.record("pipeline_news", "news_analysis", "10", "25", rc=0)
    assert ok is True
    assert captured["claimed"] == 15 and captured["persisted"] == 15
    assert captured["source"] is None          # 소스 행수는 모른다 → NULL
    assert captured["closed"] is True


def test_record_failure_never_raises(monkeypatch) -> None:
    class _Claim:
        @staticmethod
        def _open_conn():
            raise RuntimeError("db down")

    monkeypatch.setitem(sys.modules, "dq_claim", _Claim)
    assert phase_claim.record("pipeline_news", "news_analysis", "1", "2", rc=0) is False


def test_cli_always_exits_zero(monkeypatch, capsys) -> None:
    monkeypatch.setattr(phase_claim, "record", lambda *a, **k: False)
    assert phase_claim.main(["--runner", "x", "--table", "t",
                             "--before", "1", "--after", "2", "--rc", "1"]) == 0


def test_pipeline_wires_claimed_phases() -> None:
    text = (REPO / "scripts" / "full_pipeline_dd.sh").read_text(encoding="utf-8")
    for runner in ("pipeline_news", "pipeline_econ", "pipeline_financials", "pipeline_vectors"):
        assert runner in text, f"{runner} 자기신고 배선이 사라졌습니다"
    assert "run_docker_phase_claimed()" in text
    assert "table_count()" in text
