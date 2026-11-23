"""레짐 게이트 정책 고정 테스트 (2026-11-23 사용자 결정 '1').

결정: KOSDAQ risk_off 이면 단타 후보를 만들지 않는다 → 파이프라인은
`--regime-block off` 를 넘기지 않는다(스크리너 기본값 = on).

WHY: 2026-11-23 에 호스트는 차단·컨테이너는 20건 통과하는 불일치가 있었고,
원인은 data/state 마운트 누락이었다. 수리 후에도 누군가(또는 자동 틱)가
게이트를 끄면 다시 같은 불일치/추격 매매가 재발한다. 호출 형태를 테스트로 고정한다.
"""
from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
PIPELINE = REPO / "cron" / "daytrading_pipeline.sh"


def test_pipeline_exists() -> None:
    assert PIPELINE.exists(), f"파이프라인 스크립트가 없습니다: {PIPELINE}"


def test_pipeline_never_disables_regime_gate() -> None:
    text = PIPELINE.read_text(encoding="utf-8")
    calls = [ln for ln in text.splitlines()
             if "daytrading_screener.py" in ln and not ln.lstrip().startswith("#")]
    assert calls, "daytrading_screener.py 호출이 사라졌습니다"
    for line in calls:
        assert "--regime-block off" not in line, (
            "정책(2026-11-23 '1') 위반: 파이프라인이 레짐 게이트를 끄고 있습니다 — "
            "docs/DECISION_regime_gate_policy.md 참고"
        )
        assert "--regime-block on" not in line or True  # 기본값(on) 사용을 허용


def test_container_regime_mount_declared() -> None:
    """게이트 입력이 컨테이너에 보이도록 data/state 마운트가 선언되어 있어야 한다."""
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./data/state:/app/data/state" in compose, (
        "docker-compose 의 xgboost-ml 에 ./data/state 마운트가 없으면 "
        "레짐 게이트가 fail-open 으로 조용히 무력화된다"
    )
