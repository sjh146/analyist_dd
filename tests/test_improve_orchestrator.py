"""개선 큐·오케스트레이터 회귀 테스트.

WHY: 이 조각은 **사람 없이 코드를 병합**한다. 그래서 잠가야 할 것은 두 가지다 —
① 금지 경로(실주문·정책 값)를 건드린 변경은 절대 병합되지 않는다(→ needs_human)
② 큐 규칙(중복 방지·우선순위·2회 실패 시 사람 대기·근거 없는 과제 생성 금지)
"""
import importlib
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import improve_dispatch as disp  # noqa: E402
import improve_queue as iq  # noqa: E402


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    p = tmp_path / "q.json"
    monkeypatch.setattr(iq, "QUEUE", str(p))
    return p


# ── ① 금지 경로 ──────────────────────────────────────────────────────────────

def test_protected_paths_are_blocked():
    assert disp.touches_protected(["trader-agent/runner/config.py"])
    assert disp.touches_protected(["scripts/full_pipeline_dd.sh"])
    assert disp.touches_protected(["config/objective.json"])
    assert disp.touches_protected(["services/xgboost-ml/app/training/champion_promote.py"])
    assert disp.touches_protected([".env"])


def test_safe_paths_pass():
    assert disp.touches_protected([]) == []
    assert disp.touches_protected(["scripts/audit_exit_window.py", "tests/test_x.py",
                                   "docs/AUTONOMY.md"]) == []


def test_prompt_carries_prohibitions_and_acceptance():
    p = disp.build_prompt({"title": "T", "why": "W", "acceptance": ["tests/a.py 통과"]})
    for must in ("trader-agent/", "full_pipeline_dd.sh", "config/objective.json", "git 명령 금지",
                 "tests/a.py 통과"):
        assert must in p, must


# ── ② 큐 규칙 ────────────────────────────────────────────────────────────────

def test_add_dedupes_live_titles(queue):
    d = iq._load()
    a = iq.add(d, "같은 제목", "w", ["t"])
    b = iq.add(d, "같은 제목", "w", ["t"])
    assert a["id"] == b["id"] and len(d["tasks"]) == 1


def test_add_dedupes_same_evidence_even_if_title_changed(queue):
    """같은 근거(source+why)는 제목이 달라도 같은 과제다(실측: 손실원 중복 생성)."""
    d = iq._load()
    a = iq.add(d, "원래 제목", "근거 문장", ["t"], source="objective_state.top_lever")
    b = iq.add(d, "제목을 사람이 다듬음", "근거 문장", ["t"], source="objective_state.top_lever")
    assert a["id"] == b["id"] and len(d["tasks"]) == 1


def test_next_respects_priority(queue):
    d = iq._load()
    iq.add(d, "낮은 우선순위", "", ["t"], priority=3)
    hi = iq.add(d, "높은 우선순위", "", ["t"], priority=1)
    iq._save(d)
    assert iq.next_task(iq._load())["id"] == hi["id"]


def test_claim_then_finish_flow(queue):
    d = iq._load()
    t = iq.add(d, "과제", "", ["t"])
    iq._save(d)
    assert iq.main(["claim", t["id"]]) == 0
    assert iq._load()["tasks"][0]["state"] == "inflight"
    assert iq._load()["tasks"][0]["attempts"] == 1
    assert iq.main(["finish", t["id"], "--state", "merged", "--commit", "abc123"]) == 0
    assert iq._load()["tasks"][0]["commit"] == "abc123"


def test_two_failures_become_needs_human(queue):
    """무한 재시도 금지 — 2회 실패면 사람 대기로 고정한다."""
    d = iq._load()
    t = iq.add(d, "실패할 과제", "", ["t"])
    iq._save(d)
    for _ in range(2):
        iq.main(["claim", t["id"]])
        iq.main(["finish", t["id"], "--state", "rejected", "--note", "검증 실패"])
    assert iq._load()["tasks"][0]["state"] == "needs_human"
    assert iq.next_task(iq._load()) is None       # needs_human 은 다시 선택되지 않는다


def test_next_empty_returns_rc1(queue):
    iq._save(iq._load())
    assert iq.main(["next"]) == 1


def test_seed_makes_no_task_without_evidence(queue, tmp_path, monkeypatch):
    """근거가 없으면 과제를 만들지 않는다(억지 과제 금지)."""
    monkeypatch.setattr(iq, "REPO", str(tmp_path))
    d = iq._load()
    assert iq.seed(d, repo=str(tmp_path)) == []
    assert d["tasks"] == []


def test_seed_uses_objective_state_top_lever(queue, tmp_path):
    (tmp_path / "data" / "state").mkdir(parents=True)
    (tmp_path / "data" / "state" / "objective_state.json").write_text(json.dumps(
        {"top_lever": {"title": "청산창 이탈", "krw": -1814.0, "evidence": "중간"}}), encoding="utf-8")
    d = iq._load()
    added = iq.seed(d, repo=str(tmp_path))
    assert added and "청산창" in d["tasks"][0]["title"]


def test_repo_gate_is_a_policy_flag():
    """병합 게이트는 '정책'이라 값이 바뀔 수 있다(2026-10-04 종단 검증 후 true 로 전환) — 타입만 잠근다."""
    import objective
    assert isinstance(objective.load()["gates"].get("orchestrator_auto_merge"), bool)
