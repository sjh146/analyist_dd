"""KIS 분봉 수집기 + KisMinuteProvider 단위 테스트 — 전부 모킹 (실호출 금지).

커버:
 1. 분봉 파싱 — output2 샘플 → minute_bars 행 (날짜/장중시간 필터, 필수필드).
 2. HHMMSS 산술 — add_minutes.
 3. 페이지네이션 — 100건 초과 하루: 배치<건수로 자연 종료, max_pages 방어,
    단락 배치 즉시 종료, 기준시각 단조 감소.
 4. minute_bars 저장 — ON CONFLICT (stock_code, trade_date, "time") upsert.
 5. KisMinuteProvider — 30분 가격 반환 (DB 경로: "time"<=093000 / dict 경로),
    데이터 없으면 None, minute_offset 반영.
 6. daytrading_performance --provider kis 연결 (KisMinuteProvider 선택).
"""
import os
import sys
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "kis-collector"))

from kis_app.collectors.minute_collector import (MARKET_CLOSE_TIME, MARKET_OPEN_TIME,
                                             MinuteCollector, parse_minute_bars)
from kis_app.storage.postgres_storage import PostgresStorage
from kis_app.utils import add_minutes
from day_trading_engine import (DailyGapProvider, KisMinuteProvider,
                                MinutePriceProvider)


# ── 1. 분봉 파싱 ───────────────────────────────────────────────────────
class TestMinuteParse:
    FIXTURE = {
        "rt_cd": "0",
        "output2": [
            {"stck_bsop_date": "20260825", "stck_cntg_hour": "153000",
             "stck_prpr": "71200", "stck_oprc": "71100", "stck_hgpr": "71250",
             "stck_lwpr": "71000", "cntg_vol": "1500",
             "acml_tr_pbmn": "1500000000"},
            {"stck_bsop_date": "20260825", "stck_cntg_hour": "090000",
             "stck_prpr": "70000", "stck_oprc": "69900", "stck_hgpr": "70100",
             "stck_lwpr": "69800", "cntg_vol": "500", "acml_tr_pbmn": "35000000"},
            {"stck_bsop_date": "20260824", "stck_cntg_hour": "153000",
             "stck_prpr": "69500", "stck_oprc": "69500", "stck_hgpr": "69550",
             "stck_lwpr": "69400", "cntg_vol": "300", "acml_tr_pbmn": "20000000"},
            {"stck_bsop_date": "20260825", "stck_cntg_hour": "085900",
             "stck_prpr": "69000", "stck_oprc": "69000", "stck_hgpr": "69050",
             "stck_lwpr": "68900", "cntg_vol": "100", "acml_tr_pbmn": "6900000"},
            {"stck_bsop_date": "20260825", "stck_cntg_hour": "153100",
             "stck_prpr": "71300", "stck_oprc": "71300", "stck_hgpr": "71350",
             "stck_lwpr": "71200", "cntg_vol": "200", "acml_tr_pbmn": "14200000"},
        ],
    }

    def test_target_date_and_session_window_filter(self):
        rows = parse_minute_bars(self.FIXTURE, target_date="20260825")
        assert len(rows) == 2  # 153000, 090000 (085900/153100/타일자 필터)
        times = [r["time"] for r in rows]
        assert "090000" in times and "153000" in times

    def test_mapping(self):
        rows = parse_minute_bars(self.FIXTURE, target_date="20260825")
        r = next(x for x in rows if x["time"] == "090000")
        assert r["trade_date"] == date(2026, 8, 25)
        assert r["close_price"] == 70000.0
        assert r["open_price"] == 69900.0
        assert r["high_price"] == 70100.0
        assert r["low_price"] == 69800.0
        assert r["volume"] == 500
        assert r["trading_value"] == 35000000.0


# ── 2. HHMMSS 산술 ────────────────────────────────────────────────────
class TestTimeMath:
    def test_add_minutes(self):
        assert add_minutes("153000", -1) == "152900"
        assert add_minutes("090000", 30) == "093000"
        assert add_minutes("093000", 60) == "103000"
        assert add_minutes("000000", -1) == "235900"


# ── 3. 페이지네이션 ────────────────────────────────────────────────────
class FakeMinuteClient:
    """분봉 API 모킹 — 기준시각에서 과거로 fid_cnt개, 09:00 도달 시 중단."""

    def __init__(self, open_time=MARKET_OPEN_TIME):
        self.open_time = open_time
        self.calls = []

    def get_minute_chart(self, symbol, excd, input_hour, period_div="0",
                         fid_cnt=100, include_past="Y"):
        self.calls.append((symbol, excd, input_hour))
        n = int(fid_cnt)
        times = []
        t = input_hour
        while len(times) < n:
            times.append(t)
            if t == self.open_time:
                break
            t = add_minutes(t, -1)
        page = [{"stck_bsop_date": "20260825", "stck_cntg_hour": bt,
                 "stck_prpr": "10000", "stck_oprc": "10000",
                 "stck_hgpr": "10000", "stck_lwpr": "10000",
                 "cntg_vol": "1", "acml_tr_pbmn": "10000"} for bt in times]
        return {"rt_cd": "0", "output2": page}


class TestPagination:
    def test_full_day_collected_with_natural_termination(self):
        client = FakeMinuteClient()
        collector = MinuteCollector(client, None)
        bars = collector.collect_stock("005930", "KOSPI", "20260825",
                                       fid_cnt=50, max_pages=20)
        # 09:00:00~15:30:00 = 391봉, 50건/페이지 → 7페이지×50 + 41 = 8페이지
        assert len(bars) == 391
        assert len(client.calls) == 8
        assert client.calls[0][2] == MARKET_CLOSE_TIME  # 153000 시작
        refs = [c[2] for c in client.calls]
        assert all(refs[i] > refs[i + 1] for i in range(len(refs) - 1))
        assert len({b["time"] for b in bars}) == 391  # 중복 없음

    def test_max_pages_defense(self):
        client = FakeMinuteClient()
        collector = MinuteCollector(client, None)
        bars = collector.collect_stock("005930", "KOSPI", "20260825",
                                       fid_cnt=10, max_pages=10)
        assert len(client.calls) == 10  # 391봉/10건 = 40페이지 > 방어 한도
        assert len(bars) == 10 * 10

    def test_short_batch_ends_immediately(self):
        class ShortClient:
            def __init__(self):
                self.calls = []

            def get_minute_chart(self, *a, **k):
                self.calls.append(a)
                return {"rt_cd": "0", "output2": [
                    {"stck_bsop_date": "20260825", "stck_cntg_hour": "093000",
                     "stck_prpr": "10000", "stck_oprc": "10000",
                     "stck_hgpr": "10000", "stck_lwpr": "10000",
                     "cntg_vol": "1", "acml_tr_pbmn": "10000"}]}

        client = ShortClient()
        collector = MinuteCollector(client, None)
        bars = collector.collect_stock("005930", "KOSPI", "20260825",
                                       fid_cnt=100)
        assert len(client.calls) == 1
        assert len(bars) == 1

    def test_no_progress_guard(self):
        class StuckClient:
            """전체 배치(100건)를 항상 동일 시각으로 반환 → 페이지 전진 불가."""

            def __init__(self):
                self.calls = 0

            def get_minute_chart(self, *a, **k):
                self.calls += 1
                page = [{"stck_bsop_date": "20260825", "stck_cntg_hour": "092800",
                         "stck_prpr": "10000", "stck_oprc": "10000",
                         "stck_hgpr": "10000", "stck_lwpr": "10000",
                         "cntg_vol": "1", "acml_tr_pbmn": "10000"}
                        for _ in range(100)]
                return {"rt_cd": "0", "output2": page}

        client = StuckClient()
        collector = MinuteCollector(client, None)
        bars = collector.collect_stock("005930", "KOSPI", "20260825")
        assert client.calls == 2  # 2페이지째 전진 없음(092700>=092700) 감지 → 중단
        assert len(bars) == 1     # 중복 시각 봉은 1개만 저장


# ── 4. minute_bars 저장 ───────────────────────────────────────────────
class TestMinuteBarsStorage:
    class FakeCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchall(self):
            return []

        def fetchone(self):
            return None

        def close(self):
            pass

    class FakeConn:
        def __init__(self):
            self.cur = TestMinuteBarsStorage.FakeCursor()

        def cursor(self):
            return self.cur

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class FakePool:
        def __init__(self, conn):
            self.conn = conn

        def getconn(self):
            return self.conn

        def putconn(self, conn):
            pass

    def _storage(self, conn):
        cfg = SimpleNamespace(POSTGRES_HOST="h", POSTGRES_PORT=5432,
                              POSTGRES_DB="d", POSTGRES_USER="u",
                              POSTGRES_PASSWORD="p")
        return PostgresStorage(cfg, pool=self.FakePool(conn))

    def test_save_uses_on_conflict(self):
        conn = self.FakeConn()
        storage = self._storage(conn)
        row = {"trade_date": date(2026, 8, 25), "time": "093000",
               "open_price": 70000.0, "high_price": 70100.0,
               "low_price": 69800.0, "close_price": 70000.0,
               "volume": 500, "trading_value": 35000000.0}
        storage.save_minute_bars("005930", [row])
        sql, params = conn.cur.executed[-1]
        assert "INSERT INTO minute_bars" in sql
        assert 'ON CONFLICT (stock_code, trade_date, "time") DO UPDATE' in sql
        assert params[0] == "005930"
        assert params[2] == "093000"
        assert params[6] == 70000.0  # close_price


# ── 5. KisMinuteProvider ──────────────────────────────────────────────
class TestKisMinuteProvider:
    def test_is_minute_price_provider(self):
        assert issubclass(KisMinuteProvider, MinutePriceProvider)

    def test_dict_path_returns_price(self):
        prov = KisMinuteProvider({("005930", "2026-08-26"): 71200.0})
        assert prov.get_minute_price("005930", "2026-08-26") == 71200.0

    def test_dict_path_per_target_time(self):
        prov = KisMinuteProvider(
            {("005930", "2026-08-26", "093000"): 99.0})
        assert prov.get_minute_price("005930", "2026-08-26",
                                     minute_offset=30) == 99.0

    def test_dict_path_missing_returns_none(self):
        prov = KisMinuteProvider({})
        assert prov.get_minute_price("005930", "2026-08-26") is None

    def test_db_path_queries_target_time(self):
        class FakeCursor:
            def __init__(self):
                self.last_sql = None
                self.last_params = None

            def execute(self, sql, params=None):
                self.last_sql = sql
                self.last_params = params

            def fetchone(self):
                return (71200.0,)

            def close(self):
                pass

        class FakeConn:
            def __init__(self):
                self.cur = FakeCursor()

            def cursor(self):
                return self.cur

        conn = FakeConn()
        prov = KisMinuteProvider(conn)
        assert prov.get_minute_price("005930", "2026-08-26",
                                     minute_offset=30) == 71200.0
        assert '"time" <= %s' in conn.cur.last_sql
        assert conn.cur.last_params == ("005930", "2026-08-26", "093000")

    def test_db_path_minute_offset_60(self):
        class FakeCursor:
            def __init__(self):
                self.last_params = None

            def execute(self, sql, params=None):
                self.last_params = params

            def fetchone(self):
                return (10000.0,)

            def close(self):
                pass

        class FakeConn:
            def __init__(self):
                self.cur = FakeCursor()

            def cursor(self):
                return self.cur

        conn = FakeConn()
        prov = KisMinuteProvider(conn)
        prov.get_minute_price("005930", "2026-08-26", minute_offset=60)
        assert conn.cur.last_params[2] == "100000"

    def test_db_path_no_data_returns_none(self):
        class FakeCursor:
            def execute(self, sql, params=None):
                pass

            def fetchone(self):
                return None

            def close(self):
                pass

        class FakeConn:
            def cursor(self):
                return FakeCursor()

        prov = KisMinuteProvider(FakeConn())
        assert prov.get_minute_price("005930", "2026-08-26") is None


# ── 6. daytrading_performance --provider kis 연결 ─────────────────────
class TestDaytradingProviderHook:
    def _run(self, tmp_path, monkeypatch, provider_flag, gap_created, kis_created):
        import daytrading_performance as dp
        report = tmp_path / "reports"
        report.mkdir()
        df = pd.DataFrame([{"stock_code": "005930", "stock_name": "삼성전자",
                            "sector": "X", "score": 1.0,
                            "close_price": 70000.0}])
        csv_path = report / "daytrading_candidates_20260825_090000.csv"
        df.to_csv(csv_path, index=False)

        class FakeCursor:
            def execute(self, sql, params=None):
                pass

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            def close(self):
                pass

        class FakePG:
            def cursor(self):
                return FakeCursor()

            def close(self):
                pass

        monkeypatch.setattr(dp, "get_pg_conn", lambda: FakePG())

        import day_trading_engine as dte

        class Recorder(dte.MinutePriceProvider):
            def get_minute_price(self, *a, **k):
                return None

        def fake_kis(pg, open_time="090000"):
            kis_created.append((open_time,))
            return Recorder()

        def fake_gap(pg):
            gap_created.append(True)
            return Recorder()

        monkeypatch.setattr(dte, "KisMinuteProvider", fake_kis)
        monkeypatch.setattr(dte, "DailyGapProvider", fake_gap)
        argv = ["daytrading_performance", "--report-dir", str(report),
                "--min-days-ago", "0"]
        if provider_flag:
            argv += ["--provider", provider_flag]
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as ei:
            dp.main()
        return ei.value.code

    def test_provider_kis_selects_kis(self, tmp_path, monkeypatch):
        gap_created, kis_created = [], []
        code = self._run(tmp_path, monkeypatch, "kis",
                         gap_created, kis_created)
        assert code == 2  # 채점 불가(프로바이더 None) → 데이터 없음 종료
        assert len(kis_created) == 1
        assert kis_created[0] == ("090000",)
        assert gap_created == []

    def test_default_provider_is_gap(self, tmp_path, monkeypatch):
        gap_created, kis_created = [], []
        self._run(tmp_path, monkeypatch, None, gap_created, kis_created)
        assert gap_created == [True]
        assert kis_created == []
