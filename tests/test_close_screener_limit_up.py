#!/usr/bin/env python3
"""R26 회귀 — close 스크리너가 상한가·급등(발굴일 등락 ≥ 문턱) 종목을 후보에서 제외하는지.

배경(실측 2026-10-01, 87거래일 1,736행): 발행 리스트 상위3의 84%가 발굴일 +25% 이상이었고
그 집합만 평균 +7.0%/승률 81%, 살 수 있는 나머지는 −1.2%/승률 20%. 상한가로 잠긴 종목은
종가에 매수 자체가 불가능하므로(매도 물량 없음) close 경로는 '체결 불가 신호'를 계속 내보냈다.
승인(2026-11-21) 후 필터 기본 ON + 제외 건수를 sidecar 메타로 남긴다.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import close_screener as cs  # noqa: E402


def _row(code, day_change, price=5000.0, volume=100000.0, tv=5e8):
    return {
        "stock_code": code,
        "trade_date": "2026-11-20",
        "open_price": price,
        "high_price": price,
        "low_price": price,
        "close_price": price,
        "volume": volume,
        "trading_value": tv,
        "volume_surge": 1.5,
        "close_strength": 0.8,
        "ret_3d": 0.02,
        "ret_5d": 0.03,
        "day_change": day_change,
    }


def _df():
    return pd.DataFrame([
        _row("000001", 0.02),    # 정상
        _row("000002", 0.249),   # 문턱 직전 — 남는다
        _row("000003", 0.25),    # 문턱 경계(≥) — 제외
        _row("000004", 0.30),    # 상한가 — 제외
    ])


def test_limit_up_excluded_and_counted():
    stats = {}
    out = cs.filter_candidates(_df(), stats=stats)
    assert sorted(out["stock_code"]) == ["000001", "000002"]
    assert stats["excluded_limit_up"] == 2


def test_threshold_zero_disables_filter():
    stats = {}
    out = cs.filter_candidates(_df(), max_day_change=0, stats=stats)
    assert len(out) == 4
    assert stats["excluded_limit_up"] == 0


def test_custom_threshold():
    stats = {}
    out = cs.filter_candidates(_df(), max_day_change=0.30, stats=stats)
    # 0.30 은 경계 포함(≥) 이므로 000004 만 제외
    assert sorted(out["stock_code"]) == ["000001", "000002", "000003"]
    assert stats["excluded_limit_up"] == 1


def test_stats_optional_argument_still_backward_compatible():
    out = cs.filter_candidates(_df())          # stats 없이 호출해도 동작
    assert sorted(out["stock_code"]) == ["000001", "000002"]


def test_count_only_includes_rows_that_would_otherwise_pass():
    """제외 건수는 '실제로 후보가 줄어든 수'다 — 거래대금 미달 급등주는 세지 않는다."""
    df = _df()
    df.loc[len(df)] = _row("000005", 0.30, tv=1e7)     # 급등 + 거래대금 미달 → 원래도 탈락
    stats = {}
    out = cs.filter_candidates(df, stats=stats)
    assert sorted(out["stock_code"]) == ["000001", "000002"]
    assert stats["excluded_limit_up"] == 2


def _run_close_job():
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "services", "job-runner", "app", "scripts", "run_close_job.py")
    spec = importlib.util.spec_from_file_location("run_close_job_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_attach_screener_meta_reads_sidecar(tmp_path):
    import json
    csv_path = str(tmp_path / "close_out.csv")
    with open(csv_path + ".meta.json", "w", encoding="utf-8") as f:
        json.dump({"excluded_limit_up": 7, "max_day_change": 0.25}, f)
    mod = _run_close_job()
    out = mod.attach_screener_meta({"total": 0}, csv_path)
    assert out["screener_meta"]["excluded_limit_up"] == 7


def test_attach_screener_meta_absent_is_noop(tmp_path):
    mod = _run_close_job()
    out = mod.attach_screener_meta({"total": 3}, str(tmp_path / "missing.csv"))
    assert "screener_meta" not in out and out["total"] == 3
