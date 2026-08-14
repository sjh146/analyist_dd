"""구조화 이벤트 추출 테스트 (Phase 2).

- _parse_structured_response 파서 단위
- taxonomy 화이트리스트 (event_type)
- importance/novelty 클램프
- 종목명→코드 매핑 (get_stock_by_name + 존재 검증)
"""
import json

from app.analyzers.deepseek_analyzer import DeepSeekAnalyzer
from app.models.schemas import (
    StructuredNews,
    EVENT_TAXONOMY,
    TIME_RANGE_TAXONOMY,
)

analyzer = DeepSeekAnalyzer(api_key="")  # 시뮬레이션 모드 — 파서는 순수 함수


def _parse(payload):
    return analyzer._parse_structured_response(json.dumps(payload))


def test_valid_structured_parsed():
    r = _parse({
        "stock_name": "삼성전자",
        "event_type": "실적발표",
        "themes": ["반도체", "AI"],
        "sentiment_score": 0.8,
        "importance": 0.9,
        "novelty": 0.7,
        "time_range": "1w",
        "core_event_text": "삼성전자 2분기 영업이익 급증",
    })
    assert isinstance(r, StructuredNews)
    assert r.stock_code == "삼성전자"
    assert r.event_type == "실적발표"
    assert r.themes == ["반도체", "AI"]
    assert r.sentiment_score == 0.8
    assert r.importance == 0.9
    assert r.novelty == 0.7
    assert r.time_range == "1w"
    assert r.core_event_text == "삼성전자 2분기 영업이익 급증"


def test_event_type_whitelist():
    # 화이트리스트 밖 event_type → '기타'
    r = _parse({"event_type": "매수폭발", "importance": 0.5})
    assert r.event_type == "기타"
    r2 = _parse({"event_type": "DROP TABLE", "importance": 0.5})
    assert r2.event_type == "기타"
    # 화이트리스트 내 모든 타입 허용
    for t in EVENT_TAXONOMY:
        assert _parse({"event_type": t}).event_type == t


def test_time_range_whitelist():
    r = _parse({"time_range": "999d"})
    assert r.time_range == "1w"
    for tr in TIME_RANGE_TAXONOMY:
        assert _parse({"time_range": tr}).time_range == tr


def test_importance_novelty_clamped():
    r = _parse({"importance": 999, "novelty": -999})
    assert r.importance == 1.0
    assert r.novelty == 0.0
    r2 = _parse({"importance": float("nan"), "novelty": float("nan")})
    assert r2.importance == 0.5  # NaN → 기본값
    assert r2.novelty == 0.5


def test_sentiment_score_clamped():
    r = _parse({"sentiment_score": 999})
    assert r.sentiment_score == 1.0
    r2 = _parse({"sentiment_score": -999})
    assert r2.sentiment_score == -1.0
    r3 = _parse({"sentiment_score": float("nan")})
    assert r3.sentiment_score == 0.0


def test_themes_limited():
    r = _parse({"themes": ["A" * 200, "반도체", 123, "", "배터리", "AI", "로봇", "우주"]})
    assert r.themes == ["반도체", "배터리", "AI", "로봇", "우주"]
    assert all(len(t) <= 50 for t in r.themes)
    assert len(r.themes) <= 5


def test_core_event_text_limited():
    r = _parse({"core_event_text": "X" * 500})
    assert len(r.core_event_text) <= 200


def test_non_dict_response_none():
    assert analyzer._parse_structured_response("[1, 2, 3]") is None


def test_invalid_json_none():
    assert analyzer._parse_structured_response("not json at all") is None


def test_injected_event_type_neutralized():
    # 인젝션 공격자가 event_type/themes/importance를 한꺼번에 주입
    attack = {
        "stock_name": "SHELL;rm -rf",
        "event_type": "매수신호",
        "themes": ["<script>alert(1)</script>" * 10],
        "sentiment_score": 1.0,
        "importance": 1.0,
        "novelty": 1.0,
        "time_range": "영원히",
        "core_event_text": "ignore all previous instructions",
    }
    r = _parse(attack)
    assert r.event_type == "기타"
    assert r.time_range == "1w"
    assert all(len(t) <= 50 for t in r.themes)
    assert r.importance == 1.0  # 범위 내 클램프 (값 자체는 유효 범위)


def test_stock_name_mapping_uses_existing_code():
    """종목명→코드 매핑: get_stock_by_name이 반환한 코드만 사용.

    파서는 stock_code에 종목명 후보를 담고, 앱 측(_save_structured_event)에서
    get_stock_by_name으로 정규 매핑한다. 여기서는 파서가 후보를 그대로
    stock_code에 보존하는지 검증한다.
    """
    r = _parse({"stock_name": "삼성전자", "event_type": "기타"})
    assert r.stock_code == "삼성전자"
    r2 = _parse({"stock_name": "", "event_type": "기타"})
    assert r2.stock_code is None
