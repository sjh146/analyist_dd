"""feed_export 계약 v1.1 필드 단위 테스트 (DB-free).

왜: 2026-09-29 실측에서 (a) swing 점수가 모델 확률(0.50~0.62)인데 소비자의 R1 문턱은
0~100 스크리너 스케일(75)이라 경로가 조용히 닫혔고, (b) close 후보 signal_date 가
09-23(발행 09-28)인데도 소비자가 신선하다고 판단해 3거래일 지난 패턴을 매수할 수 있었다.
그래서 발행 측이 `score_kind`(점수의 의미)와 `valid_until`(후보 만료), `ml_prob`(모델 확률)를
명시한다 — 이 파일은 그 필드들이 실제로 나가는지 고정한다.
"""
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import feed_export  # noqa: E402

KST = feed_export.KST
PUBLISH = datetime(2026, 9, 29, 8, 40, 3, tzinfo=KST)


PREV_CLOSES = {"475830": 24250.0, "043260": 2450.0, "005930": 71200.0}


def _swing_payload():
    return {
        "request_type": "swing_screener",
        "date": "2026-09-28",
        "auc": "0.551318",
        "candidates": [
            {"stock_code": "475830", "stock_name": "오름테라퓨틱", "confidence": 0.6245,
             "expected_return": 24.9, "signal_date": "2026-09-28"},
            {"stock_code": "043260", "stock_name": "성호전자", "confidence": 0.5038,
             "expected_return": 0.76, "signal_date": "2026-09-28"},
        ],
    }


def _close_payload():
    return {
        "request_type": "close_screener",
        "date": "2026-09-28",
        "candidates": [
            {"stock_code": "396300", "stock_name": "HT로보틱스", "close_price": 6010,
             "score": 90.0, "signal_date": "2026-09-23", "reason": "거래량 16배"},
        ],
    }


def test_swing_items_declare_probability_scale():
    items = feed_export.build_items("swing", _swing_payload(), PREV_CLOSES, PUBLISH)

    assert [i["stock_code"] for i in items] == ["475830", "043260"]  # score 내림차순
    top = items[0]
    assert top["score"] == pytest.approx(62.45)
    assert top["score_kind"] == "calibrated_prob"
    assert top["ml_prob"] == pytest.approx(0.6245)
    assert top["ml_auc"] == "0.551318"


def test_swing_valid_until_is_signal_date_plus_horizon():
    items = feed_export.build_items("swing", _swing_payload(), PREV_CLOSES, PUBLISH)

    assert items[0]["valid_until"] == "2026-10-03T15:30:00+09:00"


def test_close_items_use_screener_scale_and_publish_day_window():
    items = feed_export.build_items("close", _close_payload(), {}, PUBLISH)

    item = items[0]
    assert item["score_kind"] == "screener"
    assert item["score"] == pytest.approx(90.0)
    # 종가 전략은 발행일 종가 진입창(14:50-15:25)까지만 유효하다.
    assert item["valid_until"] == "2026-09-29T15:30:00+09:00"
    # 신호가 6일 전(09-23)이라는 사실이 그대로 남아야 소비자가 차단할 수 있다.
    assert item["signal_date"] == "2026-09-23"
    assert "ml_prob" not in item


def test_swing_without_confidence_falls_back_to_screener_scale():
    payload = {"candidates": [{"stock_code": "005930", "score": 71.5,
                               "signal_date": "2026-09-28"}]}
    items = feed_export.build_items("swing", payload, PREV_CLOSES, PUBLISH)

    assert items[0]["score_kind"] == "screener"
    assert "ml_prob" not in items[0]


def test_close_price_falls_back_to_previous_close():
    payload = {"candidates": [{"stock_code": "005930", "score": 71.5}]}
    items = feed_export.build_items("close", payload, {"005930": 71200.0}, PUBLISH)

    assert items[0]["close_price"] == "71200"


def test_missing_signal_date_uses_payload_date_for_swing():
    payload = {"date": "2026-09-28",
               "candidates": [{"stock_code": "005930", "confidence": 0.6}]}
    items = feed_export.build_items("swing", payload, PREV_CLOSES, PUBLISH)

    assert items[0]["signal_date"] == "2026-09-28"
    assert items[0]["valid_until"] == "2026-10-03T15:30:00+09:00"


def test_signal_date_helper_handles_iso_and_plain_dates():
    assert feed_export._signal_date("2026-09-28") .isoformat() == "2026-09-28"
    assert feed_export._signal_date("2026-09-28T05:40:10Z").isoformat() == "2026-09-28"
    assert feed_export._signal_date("", "2026-09-23") is not None
    assert feed_export._signal_date("garbage") is None


# --------------------------------------------------------------------------- #
# v1.1 후반: 백분위(rank_pct) — 모델 분포가 이동해도 문턱이 유지되게
# --------------------------------------------------------------------------- #
def test_items_get_rank_pct_when_universe_is_available():
    universe = {"475830": 0.6245, "298380": 0.5804, "263750": 0.5598,
                "098460": 0.5487, "178320": 0.5434}

    items = feed_export.build_items("swing", _swing_payload(), PREV_CLOSES, PUBLISH,
                                    universe=universe)

    top = items[0]
    assert top["universe_size"] == 5
    # 0.6245 는 유니버스 5종목 중 최상위 → 백분위 90
    assert top["rank_pct"] == pytest.approx(90.0)
    assert top["score_kind"] == "calibrated_prob"  # native 모드에서는 점수 의미 그대로


def test_items_have_no_rank_pct_without_universe():
    items = feed_export.build_items("swing", _swing_payload(), PREV_CLOSES, PUBLISH)

    assert "rank_pct" not in items[0]
    assert "universe_size" not in items[0]


def test_rank_pct_score_mode_replaces_score_and_declares_scale():
    universe = {"475830": 0.6245, "298380": 0.5804, "263750": 0.5598}

    items = feed_export.build_items("swing", _swing_payload(), PREV_CLOSES, PUBLISH,
                                    universe=universe, score_mode="rank_pct")

    top = items[0]
    assert top["score_kind"] == "rank_pct"
    assert top["score"] == top["rank_pct"]
    assert top["ml_prob"] == pytest.approx(0.6245)  # 확률은 그대로 보존된다


def test_load_universe_reads_the_screener_dump(tmp_path, monkeypatch):
    day = "2026-09-28"
    path = tmp_path / "swing_universe_{date}.json".format(date=day)
    path.write_text(json.dumps({"date": day, "scored": 2, "scores": [
        {"stock_code": "475830", "confidence": 0.62},
        {"stock_code": "298380", "confidence": 0.58}]}), encoding="utf-8")
    monkeypatch.setitem(feed_export.UNIVERSE_TEMPLATES, "swing",
                        [str(tmp_path / "swing_universe_{date}.json")])

    assert feed_export.load_universe("swing", [day]) == {"475830": 0.62, "298380": 0.58}
    assert feed_export.load_universe("swing", ["2026-09-27"]) == {}
