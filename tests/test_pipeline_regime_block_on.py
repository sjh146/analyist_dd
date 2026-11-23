"""단타 후보 생성 경로의 레짐 정책 고정 테스트 (2026-11-23 저녁 개정).

정책(개정): risk_off 에서도 **후보는 만든다**. 위험은 트레이더가 조인다
(`trader_core/market_guard.evaluate_risk_off_baseline`: 사이즈 ×0.5 · 엣지 +2 · 일 2건).
레짐 게이트를 다시 켜려면 `DAYTRADING_REGIME_BLOCK=on` 환경변수로 실행한다.

WHY: 2026-11-23 낮에 "risk_off면 단타 전면 중단"으로 정했으나, 그날 저녁 사용자가
"내일부터 매매할 수 있게" 요청 → 후보 생성은 항상 허용하고 위험은 트레이더에서
줄이는 쪽으로 개정했다. 결정문서: docs/DECISION_regime_gate_policy.md
"""
from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
WRAPPERS = [
    REPO / "cron" / "daytrading_pipeline.sh",
    REPO / "cron" / "daytrading_feed_intraday.sh",
    REPO / "config" / "cron" / "daytrading_feed.sh",
]


def _screener_calls(path: pathlib.Path) -> list[str]:
    # 줄바꿈 이어짐(backslash continuation)을 한 줄로 합친 뒤 호출을 찾는다.
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    return [ln for ln in text.splitlines()
            if "daytrading_screener.py" in ln and not ln.lstrip().startswith("#")]


def test_wrappers_exist() -> None:
    for path in WRAPPERS:
        assert path.exists(), f"래퍼가 없습니다: {path}"
        assert _screener_calls(path), f"스크리너 호출이 없습니다: {path}"


def test_wrappers_allow_candidates_in_risk_off() -> None:
    """기본값은 후보 생성 허용(off) — 위험은 트레이더가 조인다."""
    for path in WRAPPERS:
        text = path.read_text(encoding="utf-8")
        assert "DAYTRADING_REGIME_BLOCK:-off" in text, (
            f"{path.name}: 레짐 게이트 기본값이 off(후보 생성 허용)가 아닙니다"
        )
        for line in _screener_calls(path):
            assert "$REGIME_ARG" in line or "--regime-block" in line, (
                f"{path.name}: 스크리너 호출에 레짐 인자가 빠졌습니다 → {line.strip()}"
            )


def test_container_regime_mount_declared() -> None:
    """게이트를 켰을 때 입력이 컨테이너에 보이도록 data/state 마운트가 선언되어야 한다."""
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./data/state:/app/data/state" in compose, (
        "docker-compose 의 xgboost-ml 에 ./data/state 마운트가 없으면 "
        "레짐 게이트가 fail-open 으로 조용히 무력화된다"
    )
