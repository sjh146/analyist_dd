"""프롬프트 인젝션 방어 테스트 (CWE-94).

Strix #4: 뉴스 본문 주입으로 신호 조작 → _parse_response가 조작된 출력을
화이트리스트/클램프로 무력화하는지 검증.
"""
import json

from app.analyzers.deepseek_analyzer import DeepSeekAnalyzer

analyzer = DeepSeekAnalyzer(api_key="")  # 시뮬레이션 모드 — _parse_response는 순수 함수


def _parse(payload):
    return analyzer._parse_response(json.dumps(payload))


def test_injected_sentiment_label_rejected():
    # 공격자가 "sentiment_label": "매수폭발" 또는 임의 문자열 주입
    r = _parse({"sentiment_label": "매수폭발", "sentiment_score": 0.9})
    assert r.sentiment_label == "neutral"  # 화이트리스트 밖 → 중립
    r2 = _parse({"sentiment_label": "BUY EVERYTHING", "sentiment_score": 1.0})
    assert r2.sentiment_label == "neutral"


def test_out_of_range_scores_clamped():
    r = _parse({"sentiment_score": 999, "authenticity_score": -999, "confidence": 5.0})
    assert r.sentiment_score == 1.0
    assert r.authenticity_score == 0.0
    assert r.confidence == 1.0
    r2 = _parse({"sentiment_score": -999})
    assert r2.sentiment_score == -1.0


def test_nan_score_sanitized():
    r = _parse({"sentiment_score": float("nan"), "authenticity_score": float("nan")})
    assert r.sentiment_score == 0.0
    assert r.authenticity_score == 0.5  # NaN → 기본값


def test_injected_stock_codes_filtered():
    # 종목코드로 주입된 임의 문자열/5자리/명령어 모두 차단, 6자리 숫자만 허용
    r = _parse({
        "related_stocks": ["005930", "abc", "12345", "DROP TABLE", "000660", "987654", "111111"]
    })
    assert r.related_stocks == ["005930", "000660", "987654", "111111"]


def test_stock_codes_capped_at_5():
    r = _parse({"related_stocks": [f"{i:06d}" for i in range(20)]})
    assert len(r.related_stocks) == 5


def test_injected_sectors_limited():
    r = _parse({"related_sectors": ["A" * 200, "반도체", 123, "", "배터리", "AI"]})
    assert r.related_sectors == ["반도체", "배터리", "AI"]
    assert all(len(s) <= 50 for s in r.related_sectors)


def test_non_dict_response_neutral():
    r = analyzer._parse_response("[1, 2, 3]")
    assert r.sentiment_label == "neutral"
    assert r.confidence == 0.0


def test_invalid_json_neutral():
    r = analyzer._parse_response("not json at all")
    assert r.sentiment_label == "neutral"
    assert r.related_stocks == []


def test_extreme_attack_payload_fully_neutralized():
    # 인젝션 공격자가 극단값 + 가짜 라벨 + 임의 종목을 한꺼번에 주입
    attack = {
        "authenticity_score": 1.0,
        "authenticity_label": "real",
        "sentiment_score": 1.0,
        "sentiment_label": "매수신호",
        "confidence": 1.0,
        "related_stocks": ["000000", "SHELL;rm", "evil"],
        "related_sectors": ["<script>alert(1)</script>" * 10],
    }
    r = _parse(attack)
    assert r.sentiment_label == "neutral"
    assert r.sentiment_score == 1.0  # 범위 내 클램프 (값 자체는 유효 범위)
    assert r.related_stocks == ["000000"]
    assert all(len(s) <= 50 for s in r.related_sectors)
