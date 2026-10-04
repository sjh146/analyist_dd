"""CEO 페르소나 헌장의 **코드 강제** 회귀 테스트.

핵심: 권한이 곧 위험이다. CEO 는 우선순위·킬·쿼터를 결정할 수 있지만, 실주문 경로·봉투·게이트 값·
승격 실행을 '결정'으로 바꿀 수 없다 — 그건 에스컬레이션(needs_human)으로만 기록된다.
그리고 근거 없는 진척 주장(evidence 빈 결정)은 거부된다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import ceo_decide as ceo  # noqa: E402


def _ok(**over):
    d = {"lever": "체결 가능 대역에서 반전 신호 재학습",
         "why": "현행 랭킹이 풀평균 대비 초과를 못 만든다(실측 Δ t<1.1)",
         "evidence": ["fillable_topk_vs_pool: k3 Δ-0.451 t-1.0"],
         "experiment_quota": 2, "kills": [], "escalations": [], "expectations": []}
    d.update(over)
    return d


def test_valid_decision_records(tmp_path):
    log = str(tmp_path / "d.jsonl")
    ok, viol = ceo.record(_ok(), path=log)
    assert ok and viol == []
    saved = [json.loads(x) for x in open(log, encoding="utf-8") if x.strip()]
    assert len(saved) == 1 and saved[0]["lever"].startswith("체결 가능")
    assert saved[0]["day"]


def test_missing_evidence_is_rejected():
    ok, viol = ceo.record(_ok(evidence=[]), path="/tmp/_never.jsonl")
    assert not ok and any("evidence" in v for v in viol)


def test_quota_out_of_range_rejected():
    assert not ceo.record(_ok(experiment_quota=9), path="/tmp/_never.jsonl")[0]
    assert not ceo.record(_ok(experiment_quota="2"), path="/tmp/_never.jsonl")[0]


def test_protected_change_must_be_escalation():
    """금지 대상(실주문 경로·봉투·게이트 값)을 '바꾸겠다'고 결정하면 거부된다."""
    for bad in ("trader-agent 설정 변경", "objective.json 의 max_entries_per_day 적용",
                "champion_promote 승격 실행"):
        ok, viol = ceo.record(_ok(lever=bad), path="/tmp/_never.jsonl")
        assert not ok, bad
        assert any("escalations" in v for v in viol), viol


def test_mentioning_protected_without_action_is_allowed():
    """금지 대상을 '언급'만 하는 것은 허용(에스컬레이션 검토 등) — 과잉 차단 금지."""
    d = _ok(escalations=["trader-agent 봉투 값 변경 승인 요청 필요"])
    ok, viol = ceo.record(d, path="/tmp/_never2.jsonl")
    assert ok, viol


def test_context_pack_has_inputs_and_charter():
    p = ceo.context_pack()
    for k in ("money", "experiments", "queue", "charter", "recent_decisions"):
        assert k in p
    assert "must_not" in p["charter"] and p["charter"]["must_not"]
    assert "돈 지표" in p["charter"]["rule"]


def test_self_audit_asks_accountability_question():
    a = ceo.self_audit()
    assert "question" in a and "움직였나" in a["question"]
    assert isinstance(a["decisions"], list)
