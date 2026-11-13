"""Tests for :mod:`bridge.signals` (ranking) and the ``/rank`` route.

The input table asserted here is the Daishin sample's, byte for byte
(``creon_algorism3.txt`` L5125-L5133 as transcribed in
``bridge/SIGNALS_NOTES.md`` §1.2) -- if someone "cleans up" the ``ord()``
arguments or the non-dense column order, these tests must fail.
"""

from __future__ import annotations

import http.client
import json
import unittest

from bridge.config import Config
from bridge.plus import BridgeError, CybosPlus
from bridge.realtime import RealtimeHub
from bridge.server import BridgeApp, ServeContext
from bridge.signals import fetch_rank, rank_inputs
from bridge.tests import support

RANK_PROGID = "CpSysDib.CpSvrNew7043"


def _rank_fake(rows: int = 2):
    """Register a fake ranking object with *rows* result rows."""
    client = support.client()
    obj = client.make(RANK_PROGID)
    obj.headers[0] = rows          # 이번 수신 종목 수
    obj.headers[1] = 200           # 전체 종목 수
    codes = ["A005930", "A000660", "A035420", "A051910", "A005380"]
    names = ["삼성전자", "SK하이닉스", "NAVER", "LG화학", "현대차"]
    obj.rows[0] = codes[:rows]
    obj.rows[1] = names[:rows]
    obj.rows[3] = ["2"] * rows     # 대비부호
    obj.rows[4] = [1250, 3000, 500, 900, 100][:rows]
    obj.rows[6] = [8515415, 2106042, 300000, 400000, 500000][:rows]
    return obj


class RankInputsTest(unittest.TestCase):
    def test_defaults_reproduce_the_sample_byte_for_byte(self) -> None:
        inputs = rank_inputs()
        self.assertEqual(
            inputs,
            {
                0: ord("0"),  # 시장: 거래소+코스닥
                1: ord("2"),  # 상승
                2: ord("1"),  # 당일
                3: 21,        # 정렬: 전일대비 상위
                4: ord("1"),  # 관리종목 제외
                5: ord("0"),  # 거래량 전체
                6: ord("0"),  # 표시: 시가대비
                7: 0,         # 등락율 시작
                8: 30,        # 등락율 끝 (상한가 제외됨!)
            },
        )

    def test_ord_style_int_switches_the_char_codes(self) -> None:
        inputs = rank_inputs(ord_style="int")
        self.assertEqual(inputs[0], 0)
        self.assertEqual(inputs[1], 2)
        self.assertEqual(inputs[3], 21)  # already a plain int in the sample

    def test_direction_and_window_are_overridable(self) -> None:
        inputs = rank_inputs(direction="down", pct_from=5, pct_to=100)
        self.assertEqual(inputs[1], ord("1"))
        self.assertEqual(inputs[7], 5)
        self.assertEqual(inputs[8], 100)

    def test_bad_values_raise(self) -> None:
        with self.assertRaises(BridgeError):
            rank_inputs(direction="sideways")
        with self.assertRaises(BridgeError):
            rank_inputs(ord_style="float")


class RankFetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()
        self.rank = _rank_fake(rows=2)

    def test_single_page_parsing(self) -> None:
        result = fetch_rank(self.session)
        self.assertEqual(self.rank.block_calls, 1)
        self.assertEqual(result["total_count"], 200)
        self.assertEqual(result["pages"], 1)
        items = result["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["code"], "005930")   # A prefix stripped
        self.assertEqual(items[0]["name"], "삼성전자")
        self.assertEqual(items[0]["diff_sign"], "2")
        self.assertEqual(items[0]["diff"], 1250)
        self.assertEqual(items[0]["volume"], 8515415)
        self.assertEqual(items[0]["rank"], 1)
        self.assertEqual(items[1]["code"], "000660")
        self.assertEqual(items[1]["rank"], 2)

    def test_inputs_are_sent_once_then_only_paged(self) -> None:
        fetch_rank(self.session)
        self.assertEqual(self.rank.inputs[3], 21)
        self.assertEqual(self.rank.inputs[8], 30)
        self.assertEqual(self.rank.inputs[0], ord("0"))

    def test_limit_caps_rows_and_stops_paging(self) -> None:
        rank = _rank_fake(rows=5)      # five parseable rows on one page
        rank._continue = True          # would page forever without the cap
        result = fetch_rank(self.session, limit=3)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(rank.block_calls, 1, "paged after the limit was reached")

    def test_pages_while_continue_is_true(self) -> None:
        fake_cls = type(self.rank)

        class _TwoPage(fake_cls):  # type: ignore[misc, valid-type]
            def __init__(self, progid):
                super().__init__(progid)
                self.pages = 0

            def BlockRequest(self) -> int:
                self.block_calls += 1
                self.pages += 1
                self._continue = self.pages < 2
                return 0

        paged = support.client().register(RANK_PROGID, _TwoPage(RANK_PROGID))
        paged.headers[0] = 2
        paged.headers[1] = 200
        paged.rows[0] = ["A005930", "A000660"]
        paged.rows[1] = ["삼성전자", "SK하이닉스"]
        paged.rows[3] = ["2", "2"]
        paged.rows[4] = [1250, 3000]
        paged.rows[6] = [10, 20]

        result = fetch_rank(self.session)
        self.assertEqual(paged.block_calls, 2)
        self.assertEqual(result["pages"], 2)
        self.assertEqual(len(result["items"]), 4)  # the fake repeats its rows

    def test_non_stock_codes_are_skipped(self) -> None:
        self.rank.rows[0] = ["A005930", "Q530031"]  # index/futures style code
        result = fetch_rank(self.session)
        self.assertEqual([item["code"] for item in result["items"]], ["005930"])

    def test_gated_on_the_quote_budget(self) -> None:
        cybos = self.fakes["cybos"]
        cybos.limit_remain_count = 0
        cybos.LimitRequestRemainTime = 60000
        with self.assertRaises(BridgeError) as cm:
            fetch_rank(self.session)
        self.assertEqual(cm.exception.code, "rate_limited")
        self.assertEqual(self.rank.block_calls, 0, "requested with no quote slot")

    def test_raw_inputs_override(self) -> None:
        fetch_rank(self.session, raw_inputs={8: 100})
        self.assertEqual(self.rank.inputs[8], 100)

    def test_not_connected(self) -> None:
        self.fakes["cybos"].IsConnect = 0
        with self.assertRaises(BridgeError) as cm:
            fetch_rank(self.session)
        self.assertEqual(cm.exception.code, "not_connected")


class RankEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()
        _rank_fake(rows=2)
        app = BridgeApp(
            plus=self.session,
            hub=RealtimeHub(self.session),
            config=Config(host="127.0.0.1", port=0),
        )
        self._ctx = ServeContext(app)
        self._ctx.__enter__()
        self.port = self._ctx.port

    def tearDown(self) -> None:
        self._ctx.__exit__(None, None, None)

    def _get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_rank_endpoint_ok(self) -> None:
        status, data = self._get("/rank?limit=5")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["rank"]), 2)
        self.assertEqual(data["rank"][0]["code"], "005930")
        self.assertEqual(data["total_count"], 200)

    def test_rank_endpoint_rejects_bad_limit(self) -> None:
        status, data = self._get("/rank?limit=abc")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"]["code"], "invalid_input")

    def test_rank_endpoint_caps_limit_at_200(self) -> None:
        status, data = self._get("/rank?limit=9999")
        self.assertTrue(data["ok"])
        self.assertLessEqual(len(data["rank"]), 200)


if __name__ == "__main__":
    unittest.main()
