"""Tests for the stratified backtest/retrain universe selector.

2026-08 회귀: 백테스트가 ORDER BY stock_code LIMIT 50 (코드순 편향 + ETF/ETN
다수 포함) 이었다. universe.py 가 무작위 층화 표본 + ETF/ETN 제외를 보장한다.
"""

from app.training.universe import ETF_ETN_PATTERNS, is_etf_etn, select_backtest_universe


class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeCur:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakePg:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCur(self._rows)


def _mk_stock(code, name, market):
    return (code, name, market, "2026-08-01")


def test_is_etf_etn_patterns():
    assert is_etf_etn("TIGER 국고채30년스트립액티브") is True
    assert is_etf_etn("하나 레버리지 콩 선물 ETN(H)") is True
    assert is_etf_etn("KODEX 자동차") is True
    assert is_etf_etn("삼성전자") is False
    assert is_etf_etn("금호석유") is False, "금 단독 패턴은 실주를 걸러내면 안 됨"
    assert is_etf_etn("하이트진로") is False
    assert is_etf_etn(None) is False


def test_patterns_all_have_percent_form():
    # 모든 패턴은 %X% 형태여야 is_etf_etn 과 일관
    for p in ETF_ETN_PATTERNS:
        assert p.startswith("%") and p.endswith("%"), p


def test_select_backtest_universe_stratified_and_deterministic():
    rows = (
        [_mk_stock(f"00{i:04d}", f"코스피주식{i}", "KOSPI") for i in range(60)]
        + [_mk_stock(f"10{i:04d}", f"코스닥주식{i}", "KOSDAQ") for i in range(40)]
        + [_mk_stock("999999", "TIGER 국고채 ETF", "KOSPI")]
    )
    pg = _FakePg(rows)

    a = select_backtest_universe(pg, n_kospi=5, n_kosdaq=4, seed=42)
    b = select_backtest_universe(pg, n_kospi=5, n_kosdaq=4, seed=42)
    assert a == b, "같은 seed면 같은 표본"
    assert len(a) == 9
    kospi_codes = {r[0] for r in rows if r[1].startswith("코스피")}
    kospi_picked = [c for c in a if c in kospi_codes]
    assert len(kospi_picked) == 5
    assert "999999" not in a, "ETF는 표본에서 제외"
    # 셔플 때문에 순서는 seed 고정 — 서로 다른 seed는 (거의) 다른 순서
    c = select_backtest_universe(pg, n_kospi=5, n_kosdaq=4, seed=7)
    assert a != c or set(a) != set(c)


def test_select_backtest_universe_falls_back_when_pool_small():
    rows = [_mk_stock("000001", "단하나", "KOSPI")]
    pg = _FakePg(rows)
    picked = select_backtest_universe(pg, n_kospi=5, n_kosdaq=4, seed=1)
    assert picked == ["000001"]
