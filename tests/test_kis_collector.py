"""KIS 일봉 수집기 단위 테스트 — 네트워크/DB 전부 모킹 (실호출 금지).

커버:
 1. 토큰 캐시 재사용 — 유효 토큰이면 재발급 curl 호출 없음 (24h 캐시).
 2. 토큰 만료 시 재발급, 파일 캐시 재사용.
 3. EGW00133(1분 1회) 모킹 — 대기 후 재시도로 발급 성공.
 4. 401(EGW00115) → 무효화 → 재발급 → 1회 재시도.
 5. OPSQ2001(필드 스키마 오류) — 설정 버그, 재시도 없이 즉시 실패.
 6. 일봉 파싱 — output2 샘플 픽스처 → market_data 행 매핑 (날짜 필터/누락 스킵).
 7. market_data upsert — ON CONFLICT (stock_code, trade_date) 중복 방지.
 8. 유니버스 — market → EXCD(KSS/KSQ) 매핑.
 9. 호출 간 딜레이 — delay 적용/0이면 무지연.
"""
import json
import os
import sys
import time
from datetime import date
from types import SimpleNamespace

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "kis-collector"))

from kis_app.client.kis_client import (KisApiError, KisClient, TokenManager,
                                   TOKEN_ENDPOINT)
from kis_app.collectors.daily_collector import (DailyCollector, market_to_excd,
                                            parse_daily_bars)
from kis_app.storage.postgres_storage import PostgresStorage

BASE_URL = "https://openapi.koreainvestment.com:9443"

TOKEN_OK = ('{"access_token":"tok-1","token_type":"Bearer","expires_in":86400}')
TOKEN_OK2 = ('{"access_token":"tok-2","token_type":"Bearer","expires_in":86400}')
TOKEN_RATE_LIMITED = ('{"rt_cd":"1","msg_cd":"EGW00133",'
                      '"msg1":"토큰발급 1분당 1회"}')


def quote_ok(output2):
    return json.dumps({"rt_cd": "0", "msg_cd": "0", "msg1": "ok",
                       "output1": {}, "output2": output2})


QUOTE_401 = '{"rt_cd":"1","msg_cd":"EGW00115","msg1":"유효하지 않은 토큰"}'
QUOTE_OPSQ2001 = ('{"rt_cd":"1","msg_cd":"OPSQ2001",'
                  '"msg1":"ERROR INPUT FIELD NOT FOUND [EXCD]"}')
QUOTE_RATE_LIMITED = ('{"rt_cd":"1","msg_cd":"EGW00225",'
                      '"msg1":"초당 거래건수를 초과하였습니다"}')


class FakeRunner:
    """curl runner 모킹 — (status, body)를 순서대로 소비, 호출 인자 기록."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, args, timeout=30):
        self.calls.append(list(args))
        if self.responses:
            return self.responses.pop(0)
        return (200, '{"rt_cd":"0","msg_cd":"0","msg1":"ok"}')

    @property
    def token_calls(self):
        return [c for c in self.calls if TOKEN_ENDPOINT in " ".join(c)]

    @property
    def quote_calls(self):
        return [c for c in self.calls if "quotations" in " ".join(c)]


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows=None):
        self.cur = FakeCursor(rows)

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


def make_client(runner, **kw):
    opts = dict(delay=0.0, jitter=0.0, retry_max=2, retry_base_delay=0.0,
                token_rate_limit_sleep=0.0, token_max_retries=2,
                token_path=None, curl_runner=runner)
    opts.update(kw)
    return KisClient("k", "s", BASE_URL, **opts)


# ── 1. 토큰 캐시 재사용 ────────────────────────────────────────────────
class TestTokenCache:
    def test_cached_token_reused(self):
        runner = FakeRunner([(200, TOKEN_OK)])
        tm = TokenManager("k", "s", BASE_URL, token_path=None,
                          rate_limit_sleep=0, max_retries=2,
                          curl_runner=runner)
        t1 = tm.get_token()
        t2 = tm.get_token()
        assert t1 == t2 == "tok-1"
        assert len(runner.token_calls) == 1  # 재발급 없음

    def test_reissue_after_expiry(self):
        runner = FakeRunner([(200, TOKEN_OK), (200, TOKEN_OK2)])
        tm = TokenManager("k", "s", BASE_URL, token_path=None,
                          rate_limit_sleep=0, max_retries=2,
                          curl_runner=runner)
        assert tm.get_token() == "tok-1"
        tm._expire_at = time.time() - 10  # 강제 만료
        assert tm.get_token() == "tok-2"
        assert len(runner.token_calls) == 2

    def test_file_cache_reused_across_instances(self, tmp_path):
        cache = str(tmp_path / "token_cache.json")
        runner1 = FakeRunner([(200, TOKEN_OK)])
        tm1 = TokenManager("k", "s", BASE_URL, token_path=cache,
                           curl_runner=runner1)
        assert tm1.get_token() == "tok-1"
        # 새 인스턴스 — 파일 캐시에서 재사용 (curl 호출 없음)
        runner2 = FakeRunner([])
        tm2 = TokenManager("k", "s", BASE_URL, token_path=cache,
                           curl_runner=runner2)
        assert tm2.get_token() == "tok-1"
        assert runner2.token_calls == []

    def test_egw00133_rate_limit_retry(self):
        runner = FakeRunner([(200, TOKEN_RATE_LIMITED), (200, TOKEN_OK)])
        sleeps = []
        tm = TokenManager("k", "s", BASE_URL, token_path=None,
                          rate_limit_sleep=60.0, max_retries=2,
                          sleep_fn=sleeps.append, curl_runner=runner)
        assert tm.get_token() == "tok-1"
        assert len(runner.token_calls) == 2
        assert sleeps == [60.0]  # 1분 제한 대기 1회


# ── 4/5. 시세 오류 처리 ────────────────────────────────────────────────
class TestQuoteErrors:
    def test_401_reissues_token_and_retries_once(self):
        runner = FakeRunner([(200, TOKEN_OK), (401, QUOTE_401),
                             (200, TOKEN_OK2),
                             (200, quote_ok('[]'))])
        client = make_client(runner)
        resp = client.get_daily_chart("005930", "J", "20260825", "20260825")
        assert resp["rt_cd"] == "0"
        assert len(runner.token_calls) == 2  # 최초 발급 + 재발급
        assert len(runner.quote_calls) == 2  # 401 + 재시도

    def test_schema_error_fails_fast_no_retry(self):
        runner = FakeRunner([(200, TOKEN_OK), (200, QUOTE_OPSQ2001)])
        client = make_client(runner, retry_max=5)
        with pytest.raises(KisApiError) as ei:
            client.get_daily_chart("005930", "J", "20260825", "20260825")
        assert ei.value.msg_cd == "OPSQ2001"
        assert len(runner.quote_calls) == 1  # 재시도 없음

    def test_rate_limited_quote_retries(self):
        runner = FakeRunner([(200, TOKEN_OK), (200, QUOTE_RATE_LIMITED),
                             (200, quote_ok('[]'))])
        client = make_client(runner)
        resp = client.get_daily_chart("005930", "J", "20260825", "20260825")
        assert resp["rt_cd"] == "0"
        assert len(runner.quote_calls) == 2


# ── 6. 일봉 파싱 ───────────────────────────────────────────────────────
class TestDailyParse:
    FIXTURE = {
        "rt_cd": "0",
        "output2": [
            {"stck_bsop_date": "20260825", "stck_clpr": "71000",
             "stck_oprc": "70000", "stck_hgpr": "71500", "stck_lwpr": "69800",
             "cntg_vol": "123456", "acml_tr_pbmn": "8712345678",
             "hts_kor_isnm": "삼성전자", "prdy_vrss": "500",
             "prdy_vrss_sign": "2", "prdy_ctrt": "0.71"},
            {"stck_bsop_date": "20260824", "stck_clpr": "70500",
             "stck_oprc": "69000", "stck_hgpr": "71000", "stck_lwpr": "68500",
             "cntg_vol": "110000", "acml_tr_pbmn": "7700000000"},
            {"stck_bsop_date": "20260826", "stck_clpr": "",
             "stck_oprc": "72000", "stck_hgpr": "72500", "stck_lwpr": "71500",
             "cntg_vol": "90000", "acml_tr_pbmn": "6000000000"},
        ],
    }

    def test_target_date_filter_and_mapping(self):
        rows = parse_daily_bars(self.FIXTURE, target_date="20260825")
        assert len(rows) == 1
        r = rows[0]
        assert r["trade_date"] == date(2026, 8, 25)
        assert r["close_price"] == 71000.0
        assert r["open_price"] == 70000.0
        assert r["high_price"] == 71500.0
        assert r["low_price"] == 69800.0
        assert r["volume"] == 123456
        assert r["trading_value"] == 8712345678.0

    def test_missing_close_row_skipped(self):
        rows = parse_daily_bars(self.FIXTURE)  # 필터 없음
        assert [str(r["trade_date"]) for r in rows] == ["2026-08-24", "2026-08-25"]
        # 08-26 (close="") 스킵, 오름차순 정렬

    def test_unknown_keys_ignored(self):
        rows = parse_daily_bars(
            {"output2": [{"stck_bsop_date": "20260825", "stck_clpr": "1000",
                          "FID_COND_MRKT_DIV_CODE": "J", "unexpected_field": "x"}]})
        assert len(rows) == 1


# ── 7. market_data upsert (중복 방지) ─────────────────────────────────
class TestMarketDataStorage:
    def _storage(self, conn):
        cfg = SimpleNamespace(POSTGRES_HOST="h", POSTGRES_PORT=5432,
                              POSTGRES_DB="d", POSTGRES_USER="u",
                              POSTGRES_PASSWORD="p")
        return PostgresStorage(cfg, pool=FakePool(conn))

    def test_save_uses_on_conflict_upsert(self):
        conn = FakeConn()
        storage = self._storage(conn)
        row = {"trade_date": date(2026, 8, 25), "open_price": 70000.0,
               "high_price": 71500.0, "low_price": 69800.0,
               "close_price": 71000.0, "volume": 123456,
               "trading_value": 8712345678.0}
        storage.save_market_data("005930", [row])
        sql, params = conn.cur.executed[-1]
        assert "INSERT INTO market_data" in sql
        assert "ON CONFLICT (stock_code, trade_date) DO UPDATE" in sql
        assert params[0] == "005930"
        assert params[1] == date(2026, 8, 25)
        assert params[5] == 71000.0

    def test_double_save_never_plain_insert(self):
        conn = FakeConn()
        storage = self._storage(conn)
        row = {"trade_date": date(2026, 8, 25), "open_price": 1.0,
               "high_price": 2.0, "low_price": 0.5, "close_price": 1.5,
               "volume": 10, "trading_value": 15000.0}
        storage.save_market_data("005930", [row])
        storage.save_market_data("005930", [row])
        inserts = [sql for sql, _ in conn.cur.executed
                   if "INSERT INTO market_data" in sql]
        assert len(inserts) == 2
        assert all("ON CONFLICT (stock_code, trade_date) DO UPDATE" in s
                   for s in inserts)

    def test_universe_query(self):
        conn = FakeConn([("005930", "KOSPI"), ("000660", "KOSDAQ")])
        storage = self._storage(conn)
        univ = storage.get_universe()
        assert univ == [("005930", "KOSPI"), ("000660", "KOSDAQ")]


# ── 8. 유니버스 → EXCD 매핑 + 수집 흐름 ────────────────────────────────
class TestDailyCollector:
    def test_market_to_excd(self):
        assert market_to_excd("KOSPI") == "J"
        assert market_to_excd("KOSDAQ") == "K"
        assert market_to_excd("UNKNOWN") == "J"
        assert market_to_excd(None) == "J"

    def test_collect_maps_excd_and_saves(self):
        class FakeClient:
            def __init__(self):
                self.daily_calls = []

            def get_daily_chart(self, symbol, excd, d1, d2, count=1):
                self.daily_calls.append((symbol, excd, d1, d2, count))
                return {"rt_cd": "0", "output2": [
                    {"stck_bsop_date": d1, "stck_clpr": "1000",
                     "stck_oprc": "990", "stck_hgpr": "1010",
                     "stck_lwpr": "985", "cntg_vol": "100",
                     "acml_tr_pbmn": "99000"}]}

        class FakeStorage:
            def __init__(self):
                self.saved = []

            def get_universe(self):
                return [("005930", "KOSPI"), ("000660", "KOSDAQ")]

            def save_market_data(self, code, rows):
                self.saved.append((code, len(rows)))
                return len(rows)

        client = FakeClient()
        storage = FakeStorage()
        summary = DailyCollector(client, storage).collect("20260825")
        assert client.daily_calls == [
            ("005930", "J", "20260825", "20260825", 5),
            ("000660", "K", "20260825", "20260825", 5),
        ]
        assert storage.saved == [("005930", 1), ("000660", 1)]
        assert summary == {"ok": 2, "no_data": 0, "fail": 0, "total": 2}

    def test_limit_truncates_universe(self):
        class FakeClient:
            def get_daily_chart(self, *a, **k):
                return {"rt_cd": "0", "output2": []}

        class FakeStorage:
            def get_universe(self):
                return [("005930", "KOSPI"), ("000660", "KOSDAQ")]

            def save_market_data(self, code, rows):
                return len(rows)

        summary = DailyCollector(FakeClient(), FakeStorage()).collect(
            "20260825", limit=1)
        assert summary["total"] == 1


# ── 9. 호출 간 딜레이 ─────────────────────────────────────────────────
class TestDelay:
    def test_delay_applied_before_quote_call(self):
        runner = FakeRunner([(200, TOKEN_OK), (200, quote_ok('[]'))])
        sleeps = []
        client = make_client(runner, delay=1.0, jitter=0.0, sleep_fn=sleeps.append)
        client.get_daily_chart("005930", "J", "20260825", "20260825")
        assert sleeps == [1.0]

    def test_zero_delay_no_sleep(self):
        runner = FakeRunner([(200, TOKEN_OK), (200, quote_ok('[]'))])
        sleeps = []
        client = make_client(runner, delay=0.0, sleep_fn=sleeps.append)
        client.get_daily_chart("005930", "J", "20260825", "20260825")
        assert sleeps == []
