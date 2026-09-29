"""ingest_trader_fills 단위 테스트 (DB 없이 파싱·집계만).

왜: 이 스크립트는 트레이더의 **실집행 결과**를 분석측 DB·리포트로 옮긴다. 매핑이나
집계가 틀리면 모델 캘리브레이션과 기여도 평가가 조용히 오염된다.
"""
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import ingest_trader_fills as itf  # noqa: E402


def _payload(date="2026-09-29", closed=None):
    return {
        "generated_at": "{0}T15:50:00+09:00".format(date),
        "source": "trader-agent",
        "date": date,
        "closed": closed if closed is not None else [],
        "opened": [],
        "summary": {},
    }


def _closed(code, net, ret, screener="swing", ml_prob=None, entry_ts=None, fees=0.0):
    record = {
        "code": code, "name": "테스트", "screener": screener, "qty": 10,
        "entry_ts": entry_ts or "2026-09-29T09:05:00+09:00",
        "entry_price": 10000.0, "exit_ts": "2026-09-29T11:05:00+09:00",
        "exit_price": 10000.0 + net / 10.0, "exit_reason": "take_profit",
        "gross_pnl": net, "fees": fees, "net_pnl": net, "ret_pct": ret,
        "hold_hours": 2.0,
    }
    if ml_prob is not None:
        record["ml_prob"] = ml_prob
    return record


def test_rows_map_closed_records():
    rows = itf.rows_from_payload(_payload(closed=[_closed("005930", 1000.0, 1.0)]))

    assert len(rows) == 1
    row = rows[0]
    assert row["fill_date"] == "2026-09-29"
    assert row["code"] == "005930"
    assert row["screener"] == "swing"
    assert row["net_pnl"] == 1000.0
    assert row["entry_ts"] == "2026-09-29T09:05:00+09:00"


def test_rows_skip_malformed_and_missing_code():
    rows = itf.rows_from_payload(_payload(closed=[
        _closed("005930", 1000.0, 1.0),
        {"name": "코드없음"},
        "not-a-dict",
    ]))

    assert [r["code"] for r in rows] == ["005930"]


def test_missing_entry_ts_falls_back_to_fill_date():
    record = _closed("005930", 1000.0, 1.0)
    record["entry_ts"] = ""

    rows = itf.rows_from_payload(_payload(closed=[record]))

    assert rows[0]["entry_ts"] == "2026-09-29"


def test_payload_without_date_is_ignored():
    assert itf.rows_from_payload({"closed": [_closed("005930", 1.0, 0.1)]}) == []


def test_aggregate_splits_screener_and_date():
    rows = itf.rows_from_payload(_payload(closed=[
        _closed("005930", 1000.0, 1.0, screener="swing"),
        _closed("000660", -500.0, -0.5, screener="close"),
    ]))

    stats = itf.aggregate(rows)

    assert stats["all"]["n"] == 2
    assert stats["all"]["net_pnl"] == 500.0
    assert stats["all"]["win_rate"] == 50.0
    assert stats["all"]["expectancy_krw"] == 250.0
    assert stats["by_screener"]["swing"]["n"] == 1
    assert stats["by_screener"]["close"]["net_pnl"] == -500.0
    assert stats["by_date"]["2026-09-29"]["n"] == 2


def test_calibration_buckets_need_enough_samples():
    few = itf.rows_from_payload(_payload(closed=[_closed("005930", 1.0, 0.1, ml_prob=0.6)]))
    assert itf.aggregate(few)["ml_calibration"] is None

    many_rows = []
    for index in range(10):
        many_rows.append(_closed("{0:06d}".format(index), 100.0, 1.0,
                                 ml_prob=0.5 + index / 100.0))
    stats = itf.aggregate(itf.rows_from_payload(_payload(closed=many_rows)))

    assert stats["scored_n"] == 10
    assert stats["ml_calibration"] is not None
    assert len(stats["ml_calibration"]) >= 2
    first = stats["ml_calibration"][0]
    assert first["prob_min"] <= first["prob_max"]
    assert first["win_rate"] == 100.0


def test_load_fill_files_is_sorted():
    with tempfile.TemporaryDirectory() as tmp:
        for day in ("2026-09-28", "2026-09-29", "2026-09-27"):
            with open(os.path.join(tmp, "fills_{0}.json".format(day)), "w", encoding="utf-8") as fh:
                json.dump(_payload(day), fh)
        with open(os.path.join(tmp, "notes.txt"), "w", encoding="utf-8") as fh:
            fh.write("ignored")

        files = itf.load_fill_files(tmp)

    assert [os.path.basename(f) for f in files] == [
        "fills_2026-09-27.json", "fills_2026-09-28.json", "fills_2026-09-29.json",
    ]


def test_main_reports_exit_3_when_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert itf.main(["--fills-dir", tmp, "--dry-run"]) == 3


def test_main_dry_run_with_data_returns_zero():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "fills_2026-09-29.json"), "w", encoding="utf-8") as fh:
            json.dump(_payload(closed=[_closed("005930", 1000.0, 1.0)]), fh)

        assert itf.main(["--fills-dir", tmp, "--dry-run"]) == 0
