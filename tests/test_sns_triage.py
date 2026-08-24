"""DB-free unit tests for DeepSeek selective triage (Phase C).

Covers:
 1. Clear positive/negative rule verdict -> LLM NOT called (used_llm=False).
 2. Ambiguous neutral text -> should_call_llm True (LLM path attempted).
 3. Important-event keyword -> should_call_llm True even if rule neutral.
 4. Cost target: a deterministic batch lands in the 10~20% LLM-call band.
 5. API-absent fail-open: triage with no DEEPSEEK_API_KEY does not raise.
"""
import asyncio
import importlib.util
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# news-analyzer 모듈은 'app' 패키지 충돌을 피해 고유 네임스페이스로 로드한다.
_loader_spec = importlib.util.spec_from_file_location(
    "sns_news_loader", os.path.join(REPO_ROOT, "tests", "sns_news_loader.py")
)
_loader = importlib.util.module_from_spec(_loader_spec)
sys.modules["sns_news_loader"] = _loader
_loader_spec.loader.exec_module(_loader)
*_, _tr = _loader.load_sns_modules()

SnsDeepSeekTriage = _tr.SnsDeepSeekTriage
IMPORTANT_KW = _tr.IMPORTANT_KW
POSITIVE_KW = _tr.POSITIVE_KW
NEGATIVE_KW = _tr.NEGATIVE_KW

# .env 는 읽지 않는다 — 테스트는 API 키 없이 규칙 전용 fail-open 경로를 검증한다.
os.environ.pop("DEEPSEEK_API_KEY", None)


@pytest.fixture
def triage():
    return SnsDeepSeekTriage()


def run(coro):
    return asyncio.run(coro)


# ── 1. 명확한 방향 → LLM 미호출 ─────────────────────────────────────────
def test_clear_positive_no_llm(triage):
    r = run(triage.triage("주가 급등 반등 대박"))
    assert r.label == "positive"
    assert r.used_llm is False


def test_clear_negative_no_llm(triage):
    r = run(triage.triage("주가 폭락 급락 하락"))
    assert r.label == "negative"
    assert r.used_llm is False


def test_should_call_llm_false_for_clear(triage):
    v = triage.rule_classify("급반등 상승세 강세 훈풍")
    assert triage.should_call_llm(v, "급반등 상승세 강세 훈풍") is False


# ── 2. 모호(중립) → LLM 호출 대상 ───────────────────────────────────────
def test_ambiguous_neutral_triggers_llm(triage):
    text = "오늘 홍길동이 뭐라 했대"
    v = triage.rule_classify(text)
    assert v.label == "neutral"
    assert triage.should_call_llm(v, text) is True


# ── 3. 중요 이벤트 → LLM 호출 대상 ──────────────────────────────────────
def test_important_event_triggers_llm(triage):
    text = "유상증자 검토"
    v = triage.rule_classify(text)
    assert triage.should_call_llm(v, text) is True
    assert any(kw in text for kw in IMPORTANT_KW)


def test_surge_flag_triggers_llm(triage):
    v = triage.rule_classify("오늘 날씨 좋네요")
    assert triage.should_call_llm(v, "오늘 날씨 좋네요", is_important_event=True) is True


# ── 4. 비용 목표 (10~20%) ───────────────────────────────────────────────
def _clear_positive_texts():
    return [
        "주가 급등 반등 대박", "급반등 상승세 강세 훈풍", "기대 낙관 유망 흑자",
        "선전 활황 상한가", "강한 모멘텀 돌파", "신고가 최고 호재", "기대감 상승세 우상향",
    ]


def _clear_negative_texts():
    return [
        "주가 폭락 급락 하락", "악재 침체 악화 손실", "적자 부진 약세 하향",
        "불황 비관 하한가", "급반락 우려 매도", "침체 악화세 부정적", "악화세 하락 위험",
    ]


def _trigger_texts():
    return [
        "오늘 홍길동이 뭐라 했대", "그냥 그런데", "뭔가 소문이 도는듯", "확인 필요하네",
        "실적 발표 예정", "유상증자 검토", "공시 나왔다", "수주 소식",
    ]


# 비용 목표는 배치 통계적 목표다. 명확한 감성 위주(대다수) + 소수 모호/중요로
# 구성된 결정적 배치에서 10~20% 구간에 도달해야 한다.
def test_llm_call_ratio_in_10_20_percent(triage):
    batch = _clear_positive_texts() * 3 + _clear_negative_texts() * 3
    batch += _trigger_texts()  # 42 clear + 8 trigger
    for text in batch:
        run(triage.triage(text))
    ratio = triage.llm_call_ratio()
    assert 0.10 <= ratio <= 0.20, f"LLM call ratio {ratio:.3f} out of 10~20% band"


# ── 5. API 부재 fail-open ───────────────────────────────────────────────
def test_fail_open_no_api_key_no_raise(triage):
    assert not os.environ.get("DEEPSEEK_API_KEY")
    r = run(triage.triage("오늘 홍길동이 뭐라 했대"))
    assert r.label == "neutral"  # 중립 폴백, 예외 없음
    assert r.used_llm is False


# ── 보조: 어휘리스트 대비 테스트 문구는 의도대로 분류된다 ──────────────
def test_clear_texts_have_no_important_keyword():
    for t in _clear_positive_texts() + _clear_negative_texts():
        assert not any(kw in t for kw in IMPORTANT_KW), f"clear text hit important kw: {t}"


def test_clear_texts_have_expected_sentiment(triage):
    for t in _clear_positive_texts():
        assert triage.rule_classify(t).label == "positive", t
    for t in _clear_negative_texts():
        assert triage.rule_classify(t).label == "negative", t
