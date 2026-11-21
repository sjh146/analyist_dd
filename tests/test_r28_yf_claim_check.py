#!/usr/bin/env python3
"""R28 check(주말-인지) 회귀 — '판정 대상 없는 날'을 미달로 세지 않는지 (2026-10-03 실측).

배경: 저녁 파이프라인은 평일 20:00 전용(`0 20 * * 1-5`)인데 R28 check 가 최근 7일 창으로
`yfinance%` + source_rows>0 행을 세면 주말·배선 직후에 매 틱 0 → '미달'을 기록한다.
판정을 '배선 이후 실제 기회가 있었는가(로그 존재 + [claim] 줄)'로 게이트한 수리를 고정한다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import r28_yf_claim_check as r28  # noqa: E402


def _mk(logs_dir, name, text=""):
    p = os.path.join(str(logs_dir), name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


# ── eligible_pipeline_logs ──────────────────────────────────────────────────
def test_eligible_only_after_deploy(tmp_path):
    _mk(tmp_path, "full_pipeline_dd_20261002_2000.log")   # 배선 전 → 제외
    _mk(tmp_path, "full_pipeline_dd_20261003_2000.log")   # 배선일(토) → 포함
    _mk(tmp_path, "full_pipeline_dd_20261005_2000.log")   # 이후 → 포함
    _mk(tmp_path, "full_pipeline_dd_badname.log")         # 파싱 불가 → 제외
    logs = r28.eligible_pipeline_logs(reports_dir=str(tmp_path))
    got = sorted(os.path.basename(p) for _d, p in logs)
    assert got == ["full_pipeline_dd_20261003_2000.log", "full_pipeline_dd_20261005_2000.log"]


def test_eligible_empty_before_deploy(tmp_path):
    _mk(tmp_path, "full_pipeline_dd_20261002_2000.log")
    assert r28.eligible_pipeline_logs(reports_dir=str(tmp_path)) == []


# ── any_claim_expected ──────────────────────────────────────────────────────
def test_claim_expected_true_when_source_positive(tmp_path):
    p = _mk(tmp_path, "full_pipeline_dd_20261005_2000.log",
            "noise\n[claim] yfinance_market_data market_data source=42 claimed=40 persisted=12\n")
    assert r28.any_claim_expected([(None, p)]) is True


def test_claim_expected_false_when_source_zero(tmp_path):
    # 휴장일: phase 가 [claim] 을 찍더라도 source=0 이면 자기신고는 기대되지 않는다.
    p = _mk(tmp_path, "full_pipeline_dd_20261006_2000.log",
            "[claim] yfinance_market_data market_data source=0 claimed=0 persisted=0\n")
    assert r28.any_claim_expected([(None, p)]) is False


def test_claim_expected_false_when_no_claim_line(tmp_path):
    p = _mk(tmp_path, "full_pipeline_dd_20261006_2000.log", "yfinance DONE\n")
    assert r28.any_claim_expected([(None, p)]) is False


def test_claim_expected_false_for_other_runner(tmp_path):
    # 다른 runner 의 [claim] 줄을 yfinance 로 오인하지 않는다.
    p = _mk(tmp_path, "full_pipeline_dd_20261006_2000.log",
            "[claim] krx_daily market_data source=99 claimed=99 persisted=99\n")
    assert r28.any_claim_expected([(None, p)]) is False


# ── decide (순수 판정) ──────────────────────────────────────────────────────
def test_decide_no_logs_is_pass():
    val, why = r28.decide([], claim_expected=False, row_count=None)
    assert val == 1
    assert "판정 대상 없음" in why


def test_decide_no_claim_line_is_pass():
    val, why = r28.decide([(None, "x.log")], claim_expected=False, row_count=None)
    assert val == 1
    assert "기대되지 않는 실행" in why


def test_decide_expected_but_zero_rows_is_fail():
    val, why = r28.decide([(None, "x.log")], claim_expected=True, row_count=0)
    assert val == 0
    assert "결함" in why


def test_decide_expected_with_rows_passes_with_count():
    val, why = r28.decide([(None, "x.log")], claim_expected=True, row_count=3)
    assert val == 3
    assert "통과" in why


def test_decide_db_failure_is_fail_closed():
    val, why = r28.decide([(None, "x.log")], claim_expected=True, row_count=None)
    assert val == 0
    assert "fail-closed" in why


# ── main() stdout 계약: stdout 의 마지막(유일) 수치가 판정값 ────────────────
def test_main_prints_single_number_on_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(r28, "REPORTS", str(tmp_path))
    monkeypatch.setattr(r28, "DEPLOY_DATE", r28.date(2026, 10, 3))
    _mk(tmp_path, "full_pipeline_dd_20261005_2000.log",
        "[claim] yfinance_market_data market_data source=7 claimed=7 persisted=7\n")
    monkeypatch.setattr(r28, "count_claim_rows", lambda since=None: 2)
    rc = r28.main()
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "2"                      # stdout 에는 숫자 하나만
    assert "통과" in out.err                            # 사유는 stderr


def test_main_weekend_no_logs_prints_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(r28, "REPORTS", str(tmp_path))
    monkeypatch.setattr(r28, "DEPLOY_DATE", r28.date(2026, 10, 3))
    rc = r28.main()                                     # 배선 이후 로그 없음(주말)
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "1"


# ── 창 정합 (2026-11-21 수리) ───────────────────────────────────────────────
# 실측 사고: 마지막 자기신고 행은 11-13(8일 전), 그 뒤 실행은 'yfinance 건너뜀'(정상 no-op)이라
# [claim] 을 찍지 않는다. 그런데 종전 check 는 **과거 로그의 [claim]** 을 근거로 **최근 7일** 행을
# 요구해 영구 미달이 됐다('고쳐도 통과 못 하는 check'). 판정 창을 로그 실행일 기준으로 맞춘다.
def test_claim_log_dates_only_claim_bearing_logs(tmp_path):
    a = _mk(tmp_path, "full_pipeline_dd_20261113_2000.log",
            "[claim] yfinance_market_data market_data source=5 claimed=5 persisted=1\n")
    b = _mk(tmp_path, "full_pipeline_dd_20261120_2000.log",
            "--- 1-1. yfinance 건너뜀 (market_data 최신 2026-11-19 >= 2026-11-16)\n")
    logs = [(r28.date(2026, 11, 13), a), (r28.date(2026, 11, 20), b)]
    assert r28.claim_log_dates(logs) == [(r28.date(2026, 11, 13), a)]


def test_main_uses_latest_claim_log_date_as_window(tmp_path, monkeypatch, capsys):
    """행 조회 기준일 = 가장 최근 [claim](source>0) 실행일 (7일 고정 창이 아니다)."""
    monkeypatch.setattr(r28, "REPORTS", str(tmp_path))
    monkeypatch.setattr(r28, "DEPLOY_DATE", r28.date(2026, 10, 3))
    _mk(tmp_path, "full_pipeline_dd_20261113_2000.log",
        "[claim] yfinance_market_data market_data source=5 claimed=5 persisted=1\n")
    _mk(tmp_path, "full_pipeline_dd_20261120_2000.log",
        "--- 1-1. yfinance 건너뜀 (market_data 최신 2026-11-19 >= 2026-11-16)\n")
    seen = {}

    def fake_count(since=None):
        seen["since"] = since
        return 1

    monkeypatch.setattr(r28, "count_claim_rows", fake_count)
    rc = r28.main()
    out = capsys.readouterr()
    assert rc == 0
    assert seen["since"] == r28.date(2026, 11, 13)      # 로그 실행일 기준
    assert out.out.strip() == "1"                       # 8일 전 행이라도 창 안이면 통과
    assert "통과" in out.err


def test_main_claim_log_without_row_still_fails(tmp_path, monkeypatch, capsys):
    """같은 창에서 행이 0 이면 여전히 미달 — 감시를 느슨하게 만든 게 아니다."""
    monkeypatch.setattr(r28, "REPORTS", str(tmp_path))
    monkeypatch.setattr(r28, "DEPLOY_DATE", r28.date(2026, 10, 3))
    _mk(tmp_path, "full_pipeline_dd_20261113_2000.log",
        "[claim] yfinance_market_data market_data source=5 claimed=5 persisted=1\n")
    monkeypatch.setattr(r28, "count_claim_rows", lambda since=None: 0)
    rc = r28.main()
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "0"
    assert "결함" in out.err
