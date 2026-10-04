"""champion_promote 의 '돈 기준' 증거 게이트 회귀 테스트 (2026-10-03 신설).

WHY: 승격 기준을 AUC 에서 **체결 가능 OOS 순기대**로 바꿨다. 근거는 실측이다 —
단일분할 AUC ↔ 다중폴드 OOS AUC 순위 상관 −0.81(거의 반대), AUC ↔ 순기대 상관 없음
(+0.10/−0.24, n=8). 그래서 "AUC 가 좋아졌다"는 승격 사유가 될 수 없고, 돈 증거(순기대·
표본·분할표본 안정성·챔피언 대비 개선)가 있어야 승격한다.
"""
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "xgboost-ml"))
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
    (d / f"training-result-20261003-000000.json").write_text(json.dumps(
        {"ensemble_auc": auc, "n_rows": 12000, "n_features": features,
         "model_aucs": {"xgboost": auc}, "up_rate": 0.48}))
    return str(d)


def _evidence(model_dir, *, pct, sessions=87, trades=1700, halves=(1.2, 0.6), auc=0.52):
    """승격 게이트가 읽는 증거 파일(scripts/model_metric_protocol_audit.py 산출 형식)."""
    rec = {"robust_auc": auc, "metric": "robust_auc",
           "expectancy_pct": pct, "expectancy_t": 1.5,
           "n_sessions": sessions, "n_trades": trades,
           "halves": {"front_pct": halves[0], "back_pct": halves[1],
                      "stable": ("both_positive" if min(halves) > 0 else "unstable")},
           "protocol": "5-fold ... · top3 · 익일 시가", "created_at": "2026-10-03T00:00:00"}
    with open(os.path.join(model_dir, "robust_oos.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f)


def _token(path, candidate, status="passed"):
    path.write_text(json.dumps({"candidate": candidate, "status": status, "detail": "신호 12건",
                                "ts": datetime.now().isoformat(timespec="seconds")}))
    return str(path)


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    cand = _make_dir(tmp_path, "champion_cand", 0.6000)
    champ = _make_dir(tmp_path, "champion", 0.5513)
    token = tmp_path / "promote_live_score_gate.json"
    monkeypatch.setattr(cp, "LIVE_SCORE_GATE_PATH", str(token))
    monkeypatch.delenv("PROMOTE_REQUIRE_ROBUST", raising=False)
    monkeypatch.delenv("PROMOTE_LIVE_SCORE_ENFORCE", raising=False)
    _token(token, "champion_cand")
    return cand, champ, token


def _run(cand, champ, **kw):
    kw.setdefault("min_auc", 0.53)
    kw.setdefault("min_improvement", 0.02)
    kw.setdefault("require_expectancy", True)
    return cp.promote(cand, champ, **kw)


def test_promotes_with_positive_expectancy_and_improvement(dirs):
    """순기대 양수 · 표본 충분 · 양쪽 절반 양수 · 챔피언 대비 개선 → 승격."""
    cand, champ, _ = dirs
    _evidence(cand, pct=0.45, halves=(0.60, 0.30))
    _evidence(champ, pct=0.10, halves=(0.20, 0.05))     # 기준선(같은 프로토콜)
    res = _run(cand, champ)
    assert res["promoted"] is True, res
    assert res["expectancy_gate"]["pct"] == 0.45


def test_refuses_without_evidence(dirs):
    """돈 증거가 없으면 승격하지 않는다(AUC 만으로는 승격 사유가 못 된다)."""
    cand, champ, _ = dirs
    _evidence(champ, pct=0.10)
    res = _run(cand, champ)
    assert res["promoted"] is False and "돈 증거 없음" in res["reason"], res


def test_refuses_when_expectancy_not_positive(dirs):
    cand, champ, _ = dirs
    _evidence(cand, pct=-0.05)
    _evidence(champ, pct=-0.20)
    res = _run(cand, champ)
    assert res["promoted"] is False and "순기대" in res["reason"], res


def test_refuses_when_sample_too_small(dirs):
    cand, champ, _ = dirs
    _evidence(cand, pct=0.90, sessions=12, trades=40)
    _evidence(champ, pct=0.10)
    res = _run(cand, champ)
    assert res["promoted"] is False and "세션" in res["reason"], res


def test_refuses_when_half_of_sample_is_negative(dirs):
    """앞 절반만 플러스인 운값은 거부한다(분할표본 안정성)."""
    cand, champ, _ = dirs
    _evidence(cand, pct=0.45, halves=(1.30, -0.40))
    _evidence(champ, pct=0.10)
    res = _run(cand, champ)
    assert res["promoted"] is False and "분할표본" in res["reason"], res


def test_refuses_when_champion_baseline_missing(dirs):
    """기준선이 없으면 비교할 수 없다 — 먼저 같은 프로토콜로 챔피언을 측정해야 한다."""
    cand, champ, _ = dirs
    _evidence(cand, pct=0.45)
    res = _run(cand, champ)
    assert res["promoted"] is False and "기준선" in res["reason"], res


def test_refuses_when_improvement_below_margin(dirs):
    cand, champ, _ = dirs
    _evidence(cand, pct=0.15)
    _evidence(champ, pct=0.10)
    res = _run(cand, champ, min_expectancy_improvement_pct=0.10)
    assert res["promoted"] is False and "개선 부족" in res["reason"], res


def test_gate_off_by_default(dirs):
    """정책을 켜지 않으면 종전 동작(AUC 기준)이 유지된다 — 되돌리기 가능해야 한다."""
    cand, champ, _ = dirs
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02)
    assert res["promoted"] is True, res
    assert "expectancy_gate" not in res


def test_robust_flag_requires_evidence_file(dirs):
    """--require-robust 는 증거 파일 부재만으로 승격을 막는다(단일분할 비교 금지)."""
    cand, champ, _ = dirs
    res = cp.promote(cand, champ, min_auc=0.53, min_improvement=0.02, require_robust=True)
    assert res["promoted"] is False and "robust_oos.json" in res["reason"], res
