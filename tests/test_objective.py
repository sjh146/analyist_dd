"""objective.json · objective.py · objective_state.py 회귀 테스트.

WHY (2026-10-03): 자율 루프의 목표·봉투·게이트가 **한 파일**에서 나온다. 이 파일이 깨지거나
트레이더 설정과 어긋나면 루프 전체가 잘못된 기준으로 돈다 → 스키마·드리프트·상태 요약을 잠근다.
"""
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import objective as obj_mod  # noqa: E402
import objective_state as st_mod  # noqa: E402


def _valid_obj():
    return {
        "version": 1, "updated": "2026-10-03",
        "goal": {"primary_metric": "fillable_oos_net_expectancy_pct",
                 "protocol": {"folds": 5},
                 "acceptance": {"primary": "expectancy_pct > 0", "min_expectancy_pct": 0.0,
                                "min_improvement_pct_over_incumbent": 0.1,
                                "min_sample_sessions": 40, "min_sample_trades": 30}},
        "envelope": {"max_entries_per_day": 3, "max_position_pct": 0.1,
                     "max_invested_pct": 0.3, "daily_loss_limit_pct": 0.03},
        "gates": {"promote_require_expectancy": True, "promote_require_robust": False},
    }


def test_repo_objective_is_valid():
    o = obj_mod.load()                                  # 실제 config/objective.json
    assert o["goal"]["primary_metric"].startswith("fillable_oos")
    assert st_mod is not None


def test_validate_catches_missing_keys():
    bad = _valid_obj()
    del bad["goal"]
    assert any("goal" in p for p in obj_mod.validate(bad))
    bad2 = _valid_obj()
    del bad2["goal"]["acceptance"]["min_sample_sessions"]
    assert any("min_sample_sessions" in p for p in obj_mod.validate(bad2))


def test_envelope_drift_detects_mismatch(tmp_path, monkeypatch):
    trader = tmp_path / "trader-agent"
    (trader / "runner").mkdir(parents=True)
    (trader / "trader_core").mkdir()
    (trader / "runner" / "config.py").write_text(
        "    r2_max_entries_per_day: int = 5\n    r4_loss_limit_pct: float = 0.03\n"
        "    r1_max_avg_score: float = 95.0\n    r1_max_avg_pct: float = 15.0\n", encoding="utf-8")
    (trader / "trader_core" / "config.py").write_text(
        "    max_position_pct: float = 0.10\n    max_invested_pct: float = 0.30\n", encoding="utf-8")
    monkeypatch.setattr(obj_mod, "_ENVELOPE_PATTERNS", {
        "max_entries_per_day": (str(trader / "runner" / "config.py"),
                                r"r2_max_entries_per_day\s*:\s*int\s*=\s*(\d+)"),
        "max_position_pct": (str(trader / "trader_core" / "config.py"),
                             r"max_position_pct\s*:\s*float\s*=\s*([0-9.]+)"),
    })
    drift = obj_mod.envelope_drift(_valid_obj())        # 목표=3, 트레이더=5 → 드리프트
    assert any("max_entries_per_day" in d for d in drift), drift
    assert not any("max_position_pct" in d for d in drift), drift   # 0.10 == 0.10 → 일치


def test_promote_flags_only_emits_supported_args():
    """champion_promote 가 실제로 받는 인자만 내보낸다(모르는 플래그 = 파이프라인 실패)."""
    flags = obj_mod.promote_flags(_valid_obj())
    allowed = {"--require-expectancy", "--min-expectancy-pct", "--min-expectancy-sessions",
               "--min-expectancy-trades", "--require-robust"}
    for f in flags:
        head = f.split()[0]
        assert head.startswith("--") and head in allowed, flags
    assert "--require-expectancy" in [f.split()[0] for f in flags]


def test_gate_set_and_get(tmp_path, monkeypatch):
    p = tmp_path / "objective.json"
    p.write_text(json.dumps(_valid_obj()), encoding="utf-8")
    monkeypatch.setattr(obj_mod, "OBJECTIVE_PATH", str(p))
    obj_mod.set_gate("promote_require_robust", True)
    assert json.loads(p.read_text(encoding="utf-8"))["gates"]["promote_require_robust"] is True
    assert obj_mod.load(str(p))["gates"]["promote_require_robust"] is True


def test_state_build_reads_scoreboard_and_flags_breaches(tmp_path, monkeypatch):
    """계기판이 없거나 수수료 미계상이면 상태가 그 사실을 드러낸다(조용한 0 금지)."""
    monkeypatch.setattr(st_mod, "REPO", str(tmp_path))
    monkeypatch.setattr(st_mod, "latest_scoreboard", lambda repo=None: (None, None))
    state = st_mod.build(repo=str(tmp_path), obj=_valid_obj())
    assert "수익 계기판 없음" in " ".join(state["envelope"]["breaches"])
    assert state["goal_metric"].startswith("fillable_oos")


def test_state_detects_envelope_overrun(tmp_path):
    sb = {"capital_utilization": {"equity": 500000, "used_pct_now": 45.0, "peak": {"concurrent": 3}},
          "realized": {"cum": {"closed": 3, "pnl": -20000.0}, "fees_unbooked_flag": True},
          "open_state": {"positions_bridge": 0}}
    env = st_mod.envelope_check(_valid_obj(), sb)
    joined = " ".join(env["breaches"])
    assert "투자비중" in joined and "실현손익" in joined and "수수료" in joined
