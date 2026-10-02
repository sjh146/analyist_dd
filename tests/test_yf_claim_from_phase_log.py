"""R28 회귀: 컨테이너 phase 의 '[claim]' 줄을 호스트가 파싱·기록하는 경로.

검증 대상: scripts/yf_claim_from_phase_log.py
  · 정상 줄 파싱(정수/'-')
  · 부재 시 no-op(기록하지 않음)
  · 여러 줄이면 **마지막** 줄 사용(총계 재신고 관례)
  · DB 실패·로그 부재가 **예외를 올리지 않는다**(자기신고가 수집을 깨면 안 된다)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import yf_claim_from_phase_log as m  # noqa: E402


def test_parse_basic():
    rec = m.parse_claim_line("[claim] yfinance_market_data market_data source=100 claimed=90 persisted=85")
    assert rec == {"runner": "yfinance_market_data", "table": "market_data",
                   "source": 100, "claimed": 90, "persisted": 85}


def test_parse_dash_means_null():
    rec = m.parse_claim_line("[claim] yfinance_market_data market_data source=100 claimed=90 persisted=-")
    assert rec["persisted"] is None
    assert rec["source"] == 100


def test_parse_negative_persisted_allowed():
    # 실측(2026-10-03): 처음 정규식이 음수 persisted 를 거부해 헬퍼가 조용히 no-op 했다(로그 한 줄 유실).
    rec = m.parse_claim_line("[claim] yfinance_market_data market_data source=1234 claimed=1200 persisted=-111")
    assert rec is not None
    assert rec["persisted"] == -111


def test_parse_absent_returns_none():
    assert m.parse_claim_line("no claim here\nrandom log line") is None


def test_parse_last_line_wins():
    txt = ("[claim] yfinance_market_data market_data source=10 claimed=9 persisted=8\n"
           "some noise\n"
           "[claim] yfinance_market_data market_data source=100 claimed=90 persisted=85\n")
    rec = m.parse_claim_line(txt)
    assert rec["source"] == 100 and rec["persisted"] == 85


def test_main_records_via_hook(tmp_path, monkeypatch):
    p = tmp_path / "phase.log"
    p.write_text("junk\n[claim] yfinance_market_data market_data source=7 claimed=6 persisted=5\n",
                 encoding="utf-8")
    captured = {}

    class FakeConn:
        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(m, "_open_conn", lambda: FakeConn())
    monkeypatch.setattr(m, "record_claim",
                        lambda conn, runner, table, **kw: captured.update(
                            runner=runner, table=table, **kw))

    assert m.main(["prog", str(p)]) == 0
    assert captured["runner"] == "yfinance_market_data"
    assert captured["table"] == "market_data"
    assert captured["source_rows"] == 7 and captured["claimed_rows"] == 6 and captured["persisted_rows"] == 5
    assert captured.get("closed") is True


def test_main_absent_line_no_record(tmp_path, monkeypatch):
    p = tmp_path / "phase.log"
    p.write_text("nothing to claim\n", encoding="utf-8")
    called = {"n": 0}
    monkeypatch.setattr(m, "_open_conn", lambda: (_ for _ in ()).throw(AssertionError("must not open")))
    monkeypatch.setattr(m, "record_claim", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert m.main(["prog", str(p)]) == 0
    assert called["n"] == 0


def test_main_db_failure_is_swallowed(tmp_path, monkeypatch):
    p = tmp_path / "phase.log"
    p.write_text("[claim] yfinance_market_data market_data source=7 claimed=6 persisted=5\n", encoding="utf-8")
    monkeypatch.setattr(m, "_open_conn", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    # 예외를 올리면 안 된다 — 자기신고 실패가 수집을 깨면 안 된다.
    assert m.main(["prog", str(p)]) == 0


def test_main_missing_file_returns_zero(monkeypatch):
    assert m.main(["prog", "/nonexistent/phase.log"]) == 0


def test_main_no_arg_returns_zero():
    assert m.main(["prog"]) == 0
