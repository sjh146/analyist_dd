"""ackman_screener DB-free unit tests (mock — FakeConn/FakeCursor + 순수 함수 픽스처)."""
import os
import re
import sys
from datetime import date, timedelta
from unittest import mock  # noqa: F401 — QA 가중치 실검증(mock.patch.dict)에 사용

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import ackman_screener as ack  # noqa: E402


AS_OF = date(2026, 8, 27)


# ── 픽스처 헬퍼 ────────────────────────────────────────────────────────


def _fund_row(ocf=None, per=None, pbr=None, op=200.0, ni=100.0, eq=1500.0,
              debt=50.0, rev=1000.0, rd="2025-12-31"):
    """fundamentals dict 1행 (load_fundamentals 출력 shape)."""
    return {"report_date": rd, "operating_profit": op, "net_income": ni,
            "total_equity": eq, "debt_ratio": debt, "revenue": rev,
            "operating_cash_flow": ocf, "per": per, "pbr": pbr}


def _ev(etype, sent=0.5, imp=1.0, text="이벤트 본문", created=None):
    """events dict 1행 (load_events 출력 shape)."""
    return {"event_type": etype, "sentiment_score": sent, "importance": imp,
            "core_event_text": text, "created_at": created or AS_OF}


def _ctx(**kw):
    """apply_vetoes ctx 기본 픽스처 (전부 통과 상태)."""
    ctx = {"debt_ratio": 100, "audit_opinion": None, "events": [],
           "latest_trade_date": AS_OF - timedelta(days=1), "signal_date": AS_OF}
    ctx.update(kw)
    return ctx


# ── Fake DB (test_screener_score.py:31-61 패턴) ────────────────────────


class FakeCursor:
    """execute no-op + last_sql/last_params 기록, fetchall/fetchone/close."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.last_sql = None
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params

    def fetchall(self):
        return [tuple(r) for r in self.rows]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class FakeConn:
    """pg-like: cursor() → FakeCursor(rows). 마지막 cursor 참조 유지 (SQL 검사용)."""

    def __init__(self, rows):
        self.rows = rows
        self.last_cursor = None

    def cursor(self):
        self.last_cursor = FakeCursor(self.rows)
        return self.last_cursor

    def close(self):
        pass


class DispCursor:
    """SQL 프래그먼트 디스패치 cursor — 쿼리 문자열·파라미터로 행 분기 (Metis m5)."""

    def __init__(self, dispatch):
        self._dispatch = dispatch
        self._rows = []

    def execute(self, sql, params=None):
        self._rows = list(self._dispatch(sql, params))

    def fetchall(self):
        return [tuple(r) for r in self._rows]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class DispConn:
    def __init__(self, dispatch):
        self._dispatch = dispatch

    def cursor(self):
        return DispCursor(self._dispatch)

    def close(self):
        pass


def _run_fixture_dispatch():
    """run_screener용 디스패치: 유니버스 3종목 (베토 1 / 정상 1 / 거래대금 미달 1)."""
    uni = [("000001", "베토종목", "IT", "2026-08-26"),
           ("000002", "정상종목", "IT", "2026-08-26"),
           ("000003", "거래대금미달", "IT", "2026-08-26")]
    meta = [("000001", "베토종목", "IT", 2e12),
            ("000002", "정상종목", "IT", 2e12),
            ("000003", "거래대금미달", "IT", 2e12)]

    def mh(code, tv, close):
        return [(code, "2026-08-24", close, close + 1000.0, close - 1000.0,
                 close, 1000.0, tv),
                (code, "2026-08-25", close, close + 1000.0, close - 1000.0,
                 close, 1000.0, tv),
                (code, "2026-08-26", close, close + 1000.0, close - 1000.0,
                 close, 1000.0, tv)]

    mh_rows = (mh("000001", 500_000_000.0, 50000.0)
               + mh("000002", 500_000_000.0, 50000.0)
               + mh("000003", 100_000_000.0, 20000.0))
    fund_rows = [("2025-12-31", 200.0, 100.0, 1500.0, 50.0, 2000.0, 150.0, 10, 10)] * 5
    peers = [("000002", 20, 20), ("000003", 30, 30)]
    sig = date(2026, 8, 27)

    def dispatch(sql, params):
        if "market_cap" in sql and "FROM stocks" in sql and "JOIN" not in sql:
            return meta
        if "FROM stocks" in sql and "JOIN market_data" in sql:
            return uni
        if "FROM market_data" in sql:
            return mh_rows
        if "financial_statements" in sql and "JOIN stocks" in sql:
            return peers
        if "financial_statements" in sql:
            return fund_rows
        if "news_event_extraction" in sql:
            code = params[0] if params else None
            if code == "000001":
                return [("CB·BW", 0.0, 1.0, "CB 발행", sig),
                        ("CB·BW", 0.0, 1.0, "CB 발행2", sig)]
            if code == "000002":
                return [("자사주", 0.5, 1.0, "자사주 매입", sig)]
            return []
        return []

    return dispatch


# ── 1~6 Quality ────────────────────────────────────────────────────────


def test_roic_consistency_full():
    assert ack._roic_consistency([0.06] * 5) == 1.0


def test_roic_consistency_missing_years_penalized():
    # 결측 연도 0점: 2/5
    assert ack._roic_consistency([0.06, 0.06, 0.0]) == 0.4


def test_quality_debt_band():
    assert ack._debt_score(50) == 1.0
    assert ack._debt_score(150) == 0.0
    assert ack._debt_score(200) == 0.0
    assert ack._debt_score(80) > ack._debt_score(120)  # 단조 감소
    assert ack._debt_score(None) == 0.0  # fail-low


def test_quality_fcf_stability():
    stable = ack._fcf_stability([0.10, 0.10, 0.10])
    volatile = ack._fcf_stability([0.30, 0.0, 0.30])
    assert stable > volatile


def test_quality_profit_volatility():
    assert ack._vol_score([0.08, 0.08, 0.08, 0.08, 0.08]) == 1.0
    # 급변: std ≈ 0.1715 > ROIC_STD_CAP(0.15) → 0.0
    # (주의: [0.30,0.0,...]은 std ≈ 0.147 < 0.15라 0.0 아님 — notepad NOTE)
    assert ack._vol_score([0.35, 0.0, 0.35, 0.0, 0.35]) == 0.0


def test_quality_insufficient_data_zero():
    rows = [_fund_row(ocf=150.0, per=10, pbr=10, rd=f"202{4 - i}-12-31")
            for i in range(2)]
    assert ack.score_quality(rows) == 0.0  # 2년 < MIN_FUND_YEARS (fail-low)


# ── 7~10 Valuation ─────────────────────────────────────────────────────


def test_valuation_zscore_mapping():
    # 결정본 강제 std+1e-9 분모로 정확 ±2 미도달(편차 ≤3e-9) → approx abs=1e-6
    assert ack._fcf_yield_z([0.1, 0.1, 0.1, 0.1, 0.6]) == pytest.approx(1.0, abs=1e-6)
    assert ack._fcf_yield_z([0.6, 0.6, 0.6, 0.6, 0.1]) == pytest.approx(0.0, abs=1e-6)
    assert ack._fcf_yield_z([0.2, 0.2, 0.2]) == pytest.approx(0.5, abs=1e-6)  # std≈0 → 중립
    assert ack._fcf_yield_z([]) == 0.0  # 유효 yield < 2 → fail-low


def test_valuation_percentile_low_per_better():
    assert ack._pct_score(10, 20) > ack._pct_score(80, 90)
    assert ack._pct_score(0, 0) == 1.0
    assert ack._pct_score(100, 100) == 0.0


def test_valuation_mos():
    # intrinsic = norm_fcf × 12.75 = 2×mc → MoS 100% → 1.0
    assert ack._mos_score(0.1568627450980392 * 1000, 1000) == pytest.approx(1.0)
    # 양수 MoS 케이스: intrinsic 1275 > mc 1000 → mos 0.275 → 0.55
    assert ack._mos_score(100.0, 1000) > 0.0
    assert ack._mos_score(1.0, 1e12) == 0.0  # MoS ≤ 0 → 0.0
    assert ack._mos_score(-100.0, 1000) == 0.0  # FCF < 0 → 0.0
    assert ack._mos_score(0.0, 1000) == 0.0  # FCF == 0 → 0.0
    assert ack._mos_score(None, 1000) == 0.0  # None → 0.0


def test_valuation_no_peers_neutral():
    m = 1000.0
    rows = [_fund_row(ocf=ocf, per=10, pbr=10)
            for ocf in [0.6 * m, 0.2 * m, 0.2 * m, 0.2 * m, 0.2 * m]]
    peers = [{"stock_code": "x", "per": 20, "pbr": 20},
             {"stock_code": "y", "per": 30, "pbr": 30}]
    with_peers = ack.score_valuation(rows, peers, m)
    no_peers = ack.score_valuation(rows, None, m)
    # 피어 부재 → pct 항 0.5 중립: 0.4×z(≈1.0) + 0.3×0.5 + 0.3×1.0 = 0.85
    assert no_peers == pytest.approx(0.85, abs=1e-6)
    assert no_peers > 0.5
    assert with_peers == pytest.approx(1.0, abs=1e-6)
    assert with_peers > no_peers


# ── 11~15 Catalyst ─────────────────────────────────────────────────────


def test_catalyst_weight_ordering():
    assert (ack._catalyst_raw([_ev("자사주")], AS_OF)
            > ack._catalyst_raw([_ev("배당")], AS_OF)
            > ack._catalyst_raw([_ev("수주")], AS_OF))


def test_catalyst_negative_sentiment_excluded():
    assert ack._catalyst_raw([_ev("자사주", sent=-0.5)], AS_OF) == 0.0
    assert ack._catalyst_raw([_ev("자사주", sent=None)], AS_OF) > 0.0  # None 포함
    assert ack._catalyst_raw([_ev("자사주", sent=0.5)], AS_OF) > 0.0


def test_catalyst_recency_decay():
    recent = ack._catalyst_raw([_ev("자사주")], AS_OF)
    old = ack._catalyst_raw([_ev("자사주", created=AS_OF - timedelta(days=91))], AS_OF)
    assert recent > old
    assert old == pytest.approx(1.0 * 1.0 * np.exp(-91.0 / ack.CATALYST_HALF_LIFE))


def test_catalyst_empty_events_zero():
    assert ack.score_catalyst([], AS_OF) == 0.0
    assert ack._catalyst_raw([], AS_OF) == 0.0


def test_catalyst_importance_scale():
    assert (ack._catalyst_raw([_ev("자사주", imp=1.0)], AS_OF)
            > ack._catalyst_raw([_ev("자사주", imp=0.0)], AS_OF))


# ── 16~17 복합 ─────────────────────────────────────────────────────────


def test_quality_composite_full_fixture():
    # op=200/ni=100(세율 0.35) → NOPAT 130, IC 1500×1.5=2250 → ROIC ≈ 0.0578 ≥ 0.05
    # FCF 마진 0.3 (std 0) → margin·stability 1.0, vol std 0 → 1.0, debt 50 → 1.0
    rows = [_fund_row(ocf=300.0, per=10, pbr=10, rd=f"202{4 - i}-12-31")
            for i in range(5)]
    assert ack.score_quality(rows) == 1.0


def test_valuation_composite_weights():
    m = 1000.0
    rows = [_fund_row(ocf=ocf, per=10, pbr=10, rd=f"202{4 - i}-12-31")
            for i, ocf in enumerate([0.6 * m, 0.2 * m, 0.2 * m, 0.2 * m, 0.2 * m])]
    peers = [{"stock_code": "x", "per": 20, "pbr": 20},
             {"stock_code": "y", "per": 30, "pbr": 30}]
    # z=2.0→1.0, pct 백분위 0→1.0, mos: median 0.2M → intrinsic 2.55M → 1.0
    assert ack.score_valuation(rows, peers, m) == pytest.approx(1.0, abs=1e-6)


# ── 18~24 하드 베토 ────────────────────────────────────────────────────


def test_veto_debt_ratio_over_limit():
    assert ack.apply_vetoes(_ctx(debt_ratio=250)) == ["부채비율_한도"]
    assert ack.apply_vetoes(_ctx(debt_ratio=150)) == []


def test_veto_cb_count_threshold():
    cb = [{"event_type": "CB·BW", "created_at": AS_OF}]
    assert ack.apply_vetoes(_ctx(events=cb * 2)) == ["CB_남발"]
    assert ack.apply_vetoes(_ctx(events=cb)) == []


def test_veto_fraud_keyword():
    fraud = [{"event_type": "규제", "core_event_text": "회사 횡령 혐의", "created_at": AS_OF}]
    clean = [{"event_type": "규제", "core_event_text": "실적 호조", "created_at": AS_OF}]
    assert ack.apply_vetoes(_ctx(events=fraud)) == ["횡령_분식"]
    assert ack.apply_vetoes(_ctx(events=clean)) == []


def test_veto_trading_halt_event():
    halt = [{"event_type": "부도·상폐·거래정지", "created_at": AS_OF}]
    assert ack.apply_vetoes(_ctx(events=halt)) == ["거래정지"]


def test_veto_trade_gap():
    assert ack.apply_vetoes(
        _ctx(latest_trade_date=AS_OF - timedelta(days=11))) == ["거래정지"]
    assert ack.apply_vetoes(
        _ctx(latest_trade_date=AS_OF - timedelta(days=5))) == []


def test_veto_audit_opinion_present_and_absent():
    assert ack.apply_vetoes(_ctx(audit_opinion="비적정")) == ["감사의견_비적정한정"]
    assert ack.apply_vetoes(_ctx(audit_opinion="한정")) == ["감사의견_비적정한정"]
    assert ack.apply_vetoes(_ctx(audit_opinion=None)) == []  # fail-open (데이터 갭)
    assert ack.apply_vetoes(_ctx(audit_opinion="적정")) == []


def test_veto_all_pass_empty():
    assert ack.apply_vetoes(_ctx(debt_ratio=100, audit_opinion="적정")) == []


# ── 25~28 복합/랭킹/출력 ────────────────────────────────────────────────


def test_ackman_score_multiplicative():
    assert ack.compute_ackman_score(0.8, 0.6, 0.5) == pytest.approx(0.24)
    assert ack.compute_ackman_score(0.8, 0.0, 0.5) == 0.0
    assert ack.compute_ackman_score(0.8, 0.6, 0.0) == 0.0
    assert ack.compute_ackman_score(0.0, 0.6, 0.5) == 0.0


def test_rank_top_n():
    df = pd.DataFrame([
        {"stock_code": c, "ackman_score": s}
        for c, s in [("A", 0.1), ("B", 0.9), ("C", 0.3), ("D", 0.7), ("E", 0.5)]
    ])
    ranked = ack.rank_candidates(df, top_n=3)
    assert len(ranked) == 3
    assert list(ranked["rank"]) == [1, 2, 3]
    assert list(ranked["ackman_score"]) == sorted(list(ranked["ackman_score"]), reverse=True)
    empty = pd.DataFrame(columns=["ackman_score"])
    out = ack.rank_candidates(empty)
    assert len(out) == 0 and "rank" in out.columns


def test_output_columns_order():
    df = pd.DataFrame([{
        "rank": 1, "stock_code": "000001", "stock_name": "A", "sector": "IT",
        "signal_date": "2026-08-19", "close_price": 50000.123,
        "ackman_score": 0.07686, "quality_score": 0.61, "valuation_score": 0.42,
        "catalyst_score": 0.30,
    }])
    rows = ack.build_output_rows(df)
    assert list(rows[0].keys()) == ack.OUTPUT_COLUMNS
    assert rows[0]["reason"] == "Q=0.61 V=0.42 C=0.30"
    assert re.fullmatch(r"Q=\d+\.\d{2} V=\d+\.\d{2} C=\d+\.\d{2}", rows[0]["reason"])
    assert rows[0]["ackman_score"] == 0.08  # 반올림 2자리
    assert rows[0]["close_price"] == 50000.12


def test_write_csv_tmp_path(tmp_path):
    df = pd.DataFrame([{
        "rank": 1, "stock_code": "000001", "stock_name": "A", "sector": "IT",
        "signal_date": "2026-08-19", "close_price": 50000.0,
        "ackman_score": 0.24, "quality_score": 0.8, "valuation_score": 0.6,
        "catalyst_score": 0.5,
    }])
    rows = ack.build_output_rows(df)
    path = ack.write_csv(rows, str(tmp_path / "ackman_candidates_20260827_120000.csv"))
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().strip()
    assert header == ",".join(ack.OUTPUT_COLUMNS)
    with open(path, encoding="utf-8") as fh:
        assert "000001" in fh.read()


# ── 29~31 로더·오케스트레이션 (FakeConn) ───────────────────────────────


def test_universe_sql_via_fakeconn():
    rows = [("000001", "삼성전자", "전기전자", "2026-08-26"),
            ("000002", "SK하이닉스", "전기전자", "2026-08-26")]
    conn = FakeConn(rows)
    result = ack.load_universe(conn, "2026-08-27")
    assert result == [("000001", "삼성전자", "전기전자", "2026-08-26"),
                      ("000002", "SK하이닉스", "전기전자", "2026-08-26")]
    sql = conn.last_cursor.last_sql
    assert "KOSPI" in sql and "KOSDAQ" in sql
    assert "HAVING COUNT(*)" in sql
    assert conn.last_cursor.last_params == (ack.MIN_MARKET_ROWS,)


def test_run_screener_happy():
    rows = ack.run_screener(DispConn(_run_fixture_dispatch()), "2026-08-27", top_n=5)
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "000002"
    assert rows[0]["rank"] == 1
    assert list(rows[0].keys()) == ack.OUTPUT_COLUMNS
    codes = {r["stock_code"] for r in rows}
    assert "000001" not in codes  # CB 베토 제외 종목 미포함
    assert "000003" not in codes  # 거래대금 미달 미포함


def test_run_screener_no_candidates():
    # 빈 유니버스 → [] 크래시 없음
    assert ack.run_screener(DispConn(lambda sql, params: []), "2026-08-27") == []

    # market_history 0행 → []
    base = _run_fixture_dispatch()

    def dispatch_empty_mh(sql, params):
        return [] if "FROM market_data" in sql else base(sql, params)

    assert ack.run_screener(DispConn(dispatch_empty_mh), "2026-08-27") == []
