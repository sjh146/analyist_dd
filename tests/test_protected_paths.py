"""금지 경로 단일 진실원 검증 — 목록이 여러 곳에서 갈라지면 그중 하나가 입구가 된다.

실측 사고(2026-10-04): 리서처 저작 경로(ensure_artifact → ask_claude.sh build)에는 검사가 아예 없어
`authoring.target` 이 실주문 경로를 가리켜도 막을 코드가 없었다. 오케스트레이터만 검사하고 있었다.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import protected_paths as pp  # noqa: E402


def test_protected_targets_are_refused_with_reason():
    for bad in ("trader-agent/tools/export_fills.py", "scripts/full_pipeline_dd.sh",
                "services/xgboost-ml/app/training/champion_promote.py",
                "config/objective.json", ".env", "data/state/kill_switch.txt",
                "scripts/gate_promote_live_score.py", "scripts/objective.py"):
        v = pp.violation(bad)
        assert v, bad
        assert "→" in v


def test_safe_targets_pass():
    for good in ("docs/AUTONOMY.md", "scripts/build_macro_features.py",
                 "services/xgboost-ml/app/feature_engine/news_event_features.py",
                 "tests/test_protected_paths.py"):
        assert pp.violation(good) is None, good


def test_violation_scans_multiple_texts():
    """경로가 프롬프트 본문에만 들어 있어도 잡아야 한다(저작 프롬프트가 실제 통로다)."""
    assert pp.violation("[구현 대상] docs/x.md", "trader-agent 설정을 바꿔라") is not None


def test_orchestrator_shares_the_same_tokens():
    """오케스트레이터(improve_dispatch.py)가 쓰는 목록과 토큰이 일치해야 한다(드리프트 방지)."""
    src = open(os.path.join(REPO, "scripts", "improve_dispatch.py"), encoding="utf-8").read().lower()
    missing = [t for t in pp.FORBIDDEN if t.lower() not in src]
    assert not missing, "오케스트레이터에 없는 금지 토큰: {0}".format(missing)


def test_guards_are_wired_into_the_authoring_paths():
    """저작 경로 두 곳(ensure_artifact·ask_claude.sh)이 실제로 이 모듈을 부른다(배선 확인)."""
    rc = open(os.path.join(REPO, "scripts", "researcher_cycle.py"), encoding="utf-8").read()
    ac = open(os.path.join(REPO, "scripts", "ask_claude.sh"), encoding="utf-8").read()
    assert "protected_paths" in rc, "researcher_cycle 에 가드 미배선"
    assert "protected_paths" in ac, "ask_claude.sh 에 가드 미배선"
