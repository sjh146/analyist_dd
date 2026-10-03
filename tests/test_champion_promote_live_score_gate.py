"""champion_promote 의 라이브 스코어 게이트 강제 — MT116 재발 방지 회귀 테스트.

WHY: val AUC 는 순위 지표라 '배포 스코어가 소비자 문턱을 넘는가'를 보장하지 않는다.
2026-10-01 승격(199피처, val 0.5548)은 배포 스코어 최대 0.4733 → 소비자 거부 → 3세션 무진입.
파이프라인은 호스트 게이트를 먼저 돌리지만 역할 틱·수동 승격은 건너뛸 수 있으므로,
공유 초크포인트(champion_promote)가 **토큰 없는 승격을 거부**하는지 고정한다.
"""
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "xgboost-ml"))
# `app` 네임스페이스 충돌 해소(services/ 12곳이 각각 app/): 전체 수집에서 다른 서비스 app 이
# 먼저 잡히면 `No module named 'app.training'` 으로 죽는다 → import 전에 고정한다.
sys.path.insert(0, REPO_ROOT)
from tests._app_path import force_app  # noqa: E402

force_app("xgboost-ml")

from app.training import champion_promote as cp  # noqa: E402


def _make_dir(root, name, auc, features=173):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for f in cp.MODEL_FILES:
        (d / f).write_bytes(b"x")
    (d / "feature_names.json").write_text(json.dumps([f"f{i}" for i in range(features)]))
    (d / "auc.txt").write_text(f"{auc}\n")
    (d / f"training-result-20261002-000000.json").write_text(json.dumps(
        {"ensemble_auc": auc, "n_rows": 12000, "n_features": features,
         "model_aucs": {"xgboost": auc}, "up_rate": 0.48}))
    return str(d)


def _champion(root, auc):
    d = _make_dir(root, "champion", auc)
    with open(os.path.join(d, "robust_auc.json"), "w", encoding="utf-8") as f:
        json.dump({"robust_auc": auc, "metric": "ensemble_auc",
                   "protocol": f"ensemble_auc on candidate val split (n_rows=12000)"}, f)
    return d


def _token(path, candidate, status, age_h=0.0, detail="후보 신호 12건"):
    path.write_text(json.dumps({
        "candidate": candidate, "status": status, "detail": detail,
        "ts": (datetime.now() - timedelta(hours=age_h)).isoformat(timespec="seconds")}))
    return str(path)


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    cand = _make_dir(tmp_path, "champion_cand", 0.6000)
    champ = _champion(tmp_path, 0.5513)
    token = tmp_path / "promote_live_score_gate.json"
    monkeypatch.setattr(cp, "LIVE_SCORE_GATE_PATH", str(token))
    monkeypatch.delenv("PROMOTE_LIVE_SCORE_ENFORCE", raising=False)
    return cand, champ, token


def test_promotes_when_gate_passed(dirs):
    cand, champ, token = dirs
    _token(token, "champion_cand", "passed")
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["promoted"] is True and res["status"] == "promoted", res
    assert res["live_score_gate"]["ok"] is True


def test_refuses_without_token(dirs):
    """토큰이 없으면 승격 거부(증거 없는 승격 금지)."""
    cand, champ, token = dirs
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["promoted"] is False and res["status"] == "blocked_live_score", res
    assert res["live_score_gate"]["ok"] is False


def test_refuses_when_gate_blocked(dirs):
    """게이트가 차단(신호 0건)이면 토큰이 blocked → 거부."""
    cand, champ, token = dirs
    _token(token, "champion_cand", "blocked", detail="후보 신호 0건")
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["status"] == "blocked_live_score", res


def test_refuses_stale_token(dirs, monkeypatch):
    """오래된 토큰은 무효(그 사이 데이터/모델이 바뀌었다)."""
    cand, champ, token = dirs
    _token(token, "champion_cand", "passed", age_h=13.0)
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["status"] == "blocked_live_score", res


def test_refuses_token_for_other_candidate(dirs):
    """다른 후보용 토큰으로는 승격할 수 없다(경로만 바꿔치기 금지)."""
    cand, champ, token = dirs
    _token(token, "challenger_cg9", "passed")
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["status"] == "blocked_live_score", res


def test_dry_run_reports_gate_but_does_not_refuse(dirs):
    """dry-run 은 판정용이므로 게이트 상태만 보고한다(거부하지 않음)."""
    cand, champ, token = dirs
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02, dry_run=True)
    assert res["status"] == "would_promote", res
    assert res["live_score_gate"]["ok"] is False       # 토큰 없음은 그대로 보인다
    assert res["promoted"] is False


def test_auc_gate_still_wins_before_live_gate(dirs):
    """AUC 게이트가 먼저 걸리면 상태는 kept_incumbent(라이브 게이트 언급 없음)."""
    cand, champ, token = dirs
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.20)
    assert res["status"] == "kept_incumbent", res


def test_enforce_zero_bypasses(dirs, monkeypatch):
    """비상 우회(ENFORCE=0)는 거부하지 않는다 — 문이 잠겨 운영이 멈추는 것을 막는다."""
    cand, champ, token = dirs
    monkeypatch.setenv("PROMOTE_LIVE_SCORE_ENFORCE", "0")
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["promoted"] is True, res


# ── 다중 폴드 OOS 지표 정책 (실측 2026-10-02: 단일분할 ↔ 다중폴드 스피어만 −0.81) ──

def _robust_oos(cand, auc, metric="robust_auc"):
    with open(os.path.join(cand, "robust_oos.json"), "w", encoding="utf-8") as f:
        json.dump({"robust_auc": auc, "metric": metric,
                   "protocol": "3-fold 연속 시간창, h=5 시장상대 중앙값, 크로스섹션 AUC"}, f)


def test_robust_oos_reported_by_default(dirs):
    """기본 정책(단일 분할)에서는 관측만 하고, OOS 값이 결과에 실린다."""
    cand, champ, token = dirs
    _token(token, "champion_cand", "passed")
    _robust_oos(cand, 0.4624)
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["promoted"] is True, res                      # 단일 분할 0.60 > 0.5513+0.02
    assert res["candidate_robust_oos"]["value"] == 0.4624, res


def test_require_robust_blocks_when_oos_is_worse(dirs, monkeypatch):
    """REQUIRE_ROBUST=1 이면 OOS 로 비교한다 — CG9 유형(단일 0.62·OOS 0.46)은 거부된다."""
    cand, champ, token = dirs
    _token(token, "champion_cand", "passed")
    _robust_oos(cand, 0.4624)
    monkeypatch.setenv("PROMOTE_REQUIRE_ROBUST", "1")
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["promoted"] is False and res["status"] == "kept_incumbent", res
    assert "robust_auc" in res["reason"] or "0.4624" in res["reason"], res


def test_require_robust_blocks_when_oos_missing(dirs, monkeypatch):
    """증거 없는 승격 금지 — OOS 지표가 없으면 승격하지 않는다."""
    cand, champ, token = dirs
    _token(token, "champion_cand", "passed")
    monkeypatch.setenv("PROMOTE_REQUIRE_ROBUST", "1")
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["promoted"] is False and res["status"] == "kept_incumbent", res
    assert "OOS" in res["reason"], res


def test_require_robust_allows_when_oos_beats_baseline(dirs, monkeypatch):
    """OOS 가 기준선을 넘으면 승격한다(정책이 잠금이 아니라 필터가 되게)."""
    cand, champ, token = dirs
    _token(token, "champion_cand", "passed")
    _robust_oos(cand, 0.5850)
    monkeypatch.setenv("PROMOTE_REQUIRE_ROBUST", "1")
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["promoted"] is True, res
