"""피드 daytrading 경로 계약 테스트 (DB-free) — docs/spec_daytrading_feed.md 그대로.

왜: 2026-10-07 저널 실측에서 decisions = swing 2,483건 · daytrading 0건 — 피드
(data/feed/screener_latest.json)의 candidates 에 daytrading 키가 없어 트레이더가
단타 후보를 평가조차 못 했다(spec 4-5행). 이 파일이 고정하는 것:
  ① 피드 candidates 에 daytrading 키가 포함되는지(발행 전체 흐름 재현)
  ② 각 후보의 필수 필드 존재(stock_code/stock_name/close_price/score/score_kind/
     signal_date/model_prob/slope_permille/volume_ratio)
  ③ score_kind == "composite" 이고 calibrated_prob 변환(×100)이 없는지,
     모델 확률은 model_prob 필드로 보존되는지
  ④ 기존 close/swing 발행 구조가 변하지 않았는지(전용 필드가 섞이지 않는지)

실측 근거(픽스처 값): data/reports/daytrading_candidates_20261007_125544.csv 1~3행.
조회 명령: head -5 data/reports/daytrading_candidates_20261007_125544.csv
  - 104200 NHN벅스   close_price=2705.0  score=64.7  kalman_slope=3.835‰  volume_surge=4.6
  - 053800 안랩      close_price=90200.0 score=64.7  kalman_slope=2.719‰  volume_surge=9.55
  - 101330 모베이스  close_price=4130.0  score=64.5  kalman_slope=4.535‰  volume_surge=7.36
  당일 실측 산출물은 model_prob 가 전부 빈 값(모델미가용) — null 보존 경로도 함께 검증하고,
  값이 있는 경로는 테스트 전용 입력(0.583 등)으로 별도 검증한다.
"""
import json
import os
import sys
from datetime import datetime

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import feed_export  # noqa: E402
import daytrading_screener  # noqa: E402

KST = feed_export.KST
PUBLISH = datetime(2026, 9, 29, 8, 40, 3, tzinfo=KST)

# 필수 필드 — spec §2 (--json-out 스키마) 그대로.
DAYTRADING_REQUIRED_FIELDS = (
    "stock_code", "stock_name", "close_price", "score", "score_kind",
    "signal_date", "model_prob", "slope_permille", "volume_ratio",
)


def _daytrading_payload():
    """당일 실측 CSV 1~3행 기반 산출물(모델미가용 → model_prob 빈 값)."""
    return {
        "generated_at": "2026-10-07T08:40:00+09:00",
        "source": "analyist_dd",
        "items": [
            {"stock_code": "104200", "stock_name": "NHN벅스", "close_price": 2705.0,
             "score": 64.7, "score_kind": "composite", "signal_date": "2026-10-06",
             "model_prob": "", "slope_permille": 3.835, "volume_ratio": 4.6},
            {"stock_code": "053800", "stock_name": "안랩", "close_price": 90200.0,
             "score": 64.7, "score_kind": "composite", "signal_date": "2026-10-06",
             "model_prob": "", "slope_permille": 2.719, "volume_ratio": 9.55},
            {"stock_code": "101330", "stock_name": "모베이스", "close_price": 4130.0,
             "score": 64.5, "score_kind": "composite", "signal_date": "2026-10-06",
             "model_prob": "", "slope_permille": 4.535, "volume_ratio": 7.36},
        ],
    }


def _close_payload():
    return {
        "request_type": "close_screener",
        "date": "2026-10-07",
        "candidates": [
            {"stock_code": "277880", "stock_name": "티에스아이", "close_price": 4100.0,
             "score": 90.0, "signal_date": "2026-10-06", "reason": "거래량 11.5배"},
            {"stock_code": "200350", "stock_name": "아티스트스튜디오", "close_price": 4290.0,
             "score": 80.0, "signal_date": "2026-10-06", "reason": "거래량 5.9배"},
            {"stock_code": "158430", "stock_name": "아톤", "close_price": 7150.0,
             "score": 70.0, "signal_date": "2026-10-06", "reason": "거래량 27.1배"},
        ],
    }


def _swing_payload():
    return {
        "request_type": "swing_screener",
        "date": "2026-10-07",
        "candidates": [
            {"stock_code": "475830", "stock_name": "오름테라퓨틱", "confidence": 0.6245,
             "close_price": 24250.0, "signal_date": "2026-10-06"},
            {"stock_code": "043260", "stock_name": "성호전자", "confidence": 0.55,
             "close_price": 2450.0, "signal_date": "2026-10-06"},
            {"stock_code": "005930", "stock_name": "삼성전자", "confidence": 0.5038,
             "close_price": 71200.0, "signal_date": "2026-10-06"},
        ],
    }


def _run_publish(tmp_path, monkeypatch):
    """3개 소스 산출물을 준비하고 feed_export.main 을 실제로 돌려 발행본을 돌려준다."""
    src = tmp_path / "src"
    src.mkdir()
    paths = {}
    for key, payload in (("close", _close_payload()), ("swing", _swing_payload()),
                         ("daytrading", _daytrading_payload())):
        p = src / f"{key}.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        paths[key] = str(p)
    out = tmp_path / "feed" / "screener_latest.json"

    # DB 의존 차단(발행 자체는 파일 산출): 종가 보정·유니버스·자기신고를 끈다.
    monkeypatch.setattr(feed_export, "load_prev_closes", lambda *a, **k: {})
    monkeypatch.setattr(feed_export, "load_universe", lambda *a, **k: {})
    monkeypatch.setattr(feed_export, "_record_feed_claim", lambda *a, **k: None)

    rc = feed_export.main([
        "--close", paths["close"], "--swing", paths["swing"],
        "--daytrading", paths["daytrading"], "--output", str(out),
        "--ignore-holiday", "--no-stats", "--min-items", "1",
        "--max-source-age-days", "9999",
    ])
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# ① 발행 전체 흐름: candidates 에 daytrading 키가 포함된다
# --------------------------------------------------------------------------- #
def test_feed_publish_includes_daytrading_key(tmp_path, monkeypatch):
    feed = _run_publish(tmp_path, monkeypatch)

    assert set(feed["candidates"]) == {"close", "swing", "daytrading"}

    items = feed["candidates"]["daytrading"]["items"]
    assert [i["stock_code"] for i in items] == ["104200", "053800", "101330"]  # score 내림차순
    for item in items:
        assert item["score_kind"] == "composite"
        for field in DAYTRADING_REQUIRED_FIELDS:
            assert field in item
        assert "ml_prob" not in item  # calibrated_prob 변환 필드가 섞이지 않는다
    top = items[0]
    # 점수 스케일 변환 금지: 64.7(복합점수)은 64.7 그대로 나가야 한다(×100 등 없음).
    assert top["score"] == pytest.approx(64.7)
    assert top["close_price"] == "2705"   # 계약 관례: 주문 지정가로 쓰이는 int 문자열
    assert top["slope_permille"] == 3.835
    assert top["volume_ratio"] == 4.6
    assert top["model_prob"] is None      # 실측 산출물은 모델미가용 → null 보존
    # "당일 매수 후보" 창: close 와 동일하게 발행일 15:30 까지 유효.
    assert top["valid_until"].endswith("T15:30:00+09:00")
    assert top["valid_until"][:10] == datetime.now(KST).date().isoformat()


# --------------------------------------------------------------------------- #
# ③ score_kind/composite · model_prob 보존 (build_items 단위)
# --------------------------------------------------------------------------- #
def test_daytrading_items_declare_composite_and_preserve_model_prob():
    payload = {"items": [
        {"stock_code": "104200", "stock_name": "NHN벅스", "close_price": 2705.0,
         "score": 64.7, "signal_date": "2026-10-06", "model_prob": "0.583",
         "slope_permille": 3.835, "volume_ratio": 4.6},
        {"stock_code": "053800", "stock_name": "안랩", "close_price": 90200.0,
         "score": 64.7, "signal_date": "2026-10-06", "model_prob": "",
         "slope_permille": 2.719, "volume_ratio": 9.55},
    ]}

    items = feed_export.build_items("daytrading", payload, {}, PUBLISH)

    assert [i["stock_code"] for i in items] == ["104200", "053800"]
    top = items[0]
    assert top["score_kind"] == "composite"
    assert top["score"] == pytest.approx(64.7)          # 변환 없음
    assert top["model_prob"] == pytest.approx(0.583)    # 모델 확률은 별도 필드로 보존
    assert items[1]["model_prob"] is None               # 모델 미가용 → null
    assert "ml_prob" not in top and "ml_prob" not in items[1]
    assert top["signal_date"] == "2026-10-06"
    # close 와 같은 당일 창(발행일 15:30).
    assert top["valid_until"] == "2026-09-29T15:30:00+09:00"


def test_daytrading_candidate_without_score_or_price_is_dropped():
    payload = {"items": [
        {"stock_code": "104200", "close_price": 2705.0, "score": 64.7},       # 정상
        {"stock_code": "053800", "close_price": 90200.0},                     # score 없음
        {"stock_code": "101330", "score": 64.5},                              # 가격 없음(보정 없음)
    ]}
    items = feed_export.build_items("daytrading", payload, {}, PUBLISH)
    assert [i["stock_code"] for i in items] == ["104200"]


# --------------------------------------------------------------------------- #
# ② 산출물(--json-out) 스키마: daytrading_screener.build_json_payload 단위
# --------------------------------------------------------------------------- #
def test_screener_json_payload_schema():
    rows = [
        {"rank": 1, "stock_code": "104200", "stock_name": "NHN벅스", "sector": "Unknown",
         "signal_date": "2026-10-06", "close_price": 2705.0, "score": 64.7,
         "kalman_trend": 27.52, "kalman_slope": 3.835, "noise_resid_std": 0.0283,
         "volume_surge": 4.6, "volatility_ann": 0.592, "model_prob": "0.583",
         "reason": "모델미가용, 칼만추세 27.5‰"},
        {"rank": 2, "stock_code": "053800", "stock_name": "안랩", "sector": "Unknown",
         "signal_date": "2026-10-06", "close_price": 90200.0, "score": 64.7,
         "kalman_trend": 38.64, "kalman_slope": 2.719, "noise_resid_std": 0.034,
         "volume_surge": 9.55, "volatility_ann": 0.588, "model_prob": "",
         "reason": "모델미가용, 칼만추세 38.6‰"},
    ]

    payload = daytrading_screener.build_json_payload(
        rows, generated_at=datetime(2026, 10, 7, 8, 40, 0, tzinfo=KST))

    assert set(payload) == {"generated_at", "source", "items"}
    assert payload["source"] == "analyist_dd"
    assert payload["generated_at"] == "2026-10-07T08:40:00+09:00"
    top = payload["items"][0]
    for field in DAYTRADING_REQUIRED_FIELDS:
        assert field in top
    assert top["score_kind"] == "composite"
    assert top["model_prob"] == pytest.approx(0.583)
    assert top["slope_permille"] == 3.835     # kalman_slope(‰) 그대로
    assert top["volume_ratio"] == 4.6         # volume_surge(배수) 그대로
    assert payload["items"][1]["model_prob"] is None


# --------------------------------------------------------------------------- #
# ④ 기존 close/swing 발행 구조 불변 (발행 전체 흐름에서 대조)
# --------------------------------------------------------------------------- #
def test_close_swing_publish_structure_unchanged(tmp_path, monkeypatch):
    feed = _run_publish(tmp_path, monkeypatch)

    for key, payload in (("close", _close_payload()), ("swing", _swing_payload())):
        published = feed["candidates"][key]["items"]
        expected = feed_export.build_items(key, payload, {}, None)
        assert [i["stock_code"] for i in published] == [i["stock_code"] for i in expected]
        for got, want in zip(published, expected):
            # valid_until 은 발행 시각(now) 기준이라 제외하고 나머지 구조가 비트 동일해야 한다.
            assert {k: v for k, v in got.items() if k != "valid_until"} == \
                   {k: v for k, v in want.items() if k != "valid_until"}
            # daytrading 전용 필드가 기존 경로에 새지 않는다.
            assert "model_prob" not in got
            assert "slope_permille" not in got
            assert "volume_ratio" not in got
    # 기존 키의 점수 의미 선언도 그대로다.
    assert feed["candidates"]["close"]["items"][0]["score_kind"] == "screener"
    assert feed["candidates"]["swing"]["items"][0]["score_kind"] == "calibrated_prob"


# --------------------------------------------------------------------------- #
# 라이브 피드(data/feed/screener_latest.json) 검증 — 발행 크론이 daytrading 을
# 넣은 뒤부터 활성화된다. 아직 미발행이면 skip(구현 시점 실측: candidates = {close, swing}).
# --------------------------------------------------------------------------- #
def test_live_feed_daytrading_structure_when_published():
    path = os.path.join(REPO_ROOT, "data", "feed", "screener_latest.json")
    if not os.path.exists(path):
        pytest.skip("live 피드 파일 없음")
    data = json.load(open(path, encoding="utf-8"))
    candidates = data.get("candidates") or {}
    if "daytrading" not in candidates:
        pytest.skip("live 피드에 daytrading 아직 미발행 — 발행 크론 실행 후 이 검증이 활성화된다")
    for item in candidates["daytrading"].get("items", []):
        assert item["score_kind"] == "composite"
        for field in DAYTRADING_REQUIRED_FIELDS:
            assert field in item
    for key in ("close", "swing"):
        for item in candidates.get(key, {}).get("items", []):
            assert "model_prob" not in item
            assert "slope_permille" not in item
