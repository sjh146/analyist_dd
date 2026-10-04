"""T1 탐색(실험 러너) 회귀 테스트 — 체결성 가드가 핵심.

WHY: 필터 없는 조합은 '살 수 없는 종목'을 포함해 +5.23%p 같은 착시를 만든다(실측 2026-10-02).
그래서 ① 격자에 cap=None 을 두지 않고 ② 판정에서도 cap 미설정/과대를 무조건 탈락시킨다.
또 사전등록 격자·현행 조합·수용 기준이 objective.json 과 어긋나지 않게 잠근다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import experiment_runner as er  # noqa: E402


def _obj():
    return {"goal": {"acceptance": {"primary": "expectancy_pct > 0", "min_expectancy_pct": 0.0,
                                    "min_improvement_pct_over_incumbent": 0.1,
                                    "min_sample_sessions": 40, "min_sample_trades": 30}}}


def _res(key, cap, exp, sessions=87, trades=1300, stable="both_positive", topk=3, exit="next_open"):
    return {key: {"combo": {"topk": topk, "max_day_chg": cap, "exit": exit},
                  "avg_pct": exp, "t_stat": 1.0, "n_sessions": sessions, "n_trades": trades,
                  "halves": {"front": exp, "back": exp, "stable": stable}}}


def test_grid_has_no_unfiltered_cap():
    assert None not in er.GRID["max_day_chg"]
    assert max(er.GRID["max_day_chg"]) <= er.MAX_FILLABLE_CAP


def test_no_fillability_filter_never_passes():
    """cap=None(무필터)은 순기대가 아무리 좋아도 탈락 — 상한가 착시."""
    r = _res("k3_capNone_next_open", None, 5.2328)
    v = er.verdicts(r, _obj())["k3_capNone_next_open"]
    assert v["pass"] is False and "체결성" in " ".join(v["why"]), v


def test_cap_above_fillable_bound_never_passes():
    r = _res("k3_cap30_next_open", 30.0, 2.0)
    assert er.verdicts(r, _obj())["k3_cap30_next_open"]["pass"] is False


def test_negative_expectancy_fails():
    r = _res("k3_cap10.0_next_open", 10.0, -0.32)
    v = er.verdicts(r, _obj())["k3_cap10.0_next_open"]
    assert v["pass"] is False and any("순기대" in w for w in v["why"]), v


def test_thin_sample_fails():
    r = _res("k1_cap15.0_next_open", 15.0, 0.5, sessions=10, trades=20)
    v = er.verdicts(r, _obj())["k1_cap15.0_next_open"]
    assert v["pass"] is False and any("세션" in w for w in v["why"]), v


def test_unstable_halves_fail():
    r = _res("k2_cap20.0_next_open", 20.0, 0.5, stable="unstable")
    v = er.verdicts(r, _obj())["k2_cap20.0_next_open"]
    assert v["pass"] is False and any("분할표본" in w for w in v["why"]), v


def test_must_beat_incumbent_by_margin():
    """같은 CSV 안의 현행 조합 대비 개선폭 미달이면 탈락(외부 측정과 섞지 않는다)."""
    r = _res(er.combo_key(er.INCUMBENT), 25.0, 0.10, topk=3, exit="next_open")
    r.update(_res("k3_cap20.0_next_open", 20.0, 0.15, topk=3, exit="next_open"))
    v = er.verdicts(r, _obj())
    assert v[er.combo_key(er.INCUMBENT)]["pass"] is True
    assert v["k3_cap20.0_next_open"]["pass"] is False       # +0.05%p < +0.1%p 마진
    assert v["k3_cap20.0_next_open"]["delta_vs_incumbent"] == 0.05


def test_everything_passes_when_conditions_met():
    r = _res(er.combo_key(er.INCUMBENT), 25.0, 0.10, topk=3,
             exit="next_open") if False else _res("k3_cap25.0_next_open", 25.0, 0.10)
    r.update(_res("k2_cap15.0_next_open", 15.0, 0.60, topk=2))
    v = er.verdicts(r, _obj())
    assert v["k2_cap15.0_next_open"]["pass"] is True, v


def test_grid_covers_reversal_and_gate_axes():
    """탐색 공간이 '점수 상위'만 보지 않는다 — 역추세(하위·소상승)와 게이트 조건을 포함해야 한다."""
    import pandas as pd
    assert "score_bottom" in er.GRID["select"] and "daychg_low" in er.GRID["select"]
    assert True in er.GRID["gated"] and False in er.GRID["gated"]
    t = pd.DataFrame({"score": [1.0, 2.0, 3.0], "day_change_pct": [5.0, 1.0, -2.0]})
    v = er._select_variants(t)
    # score 하위 = 원래 최저 점수(1.0)가 1등이 된다
    assert v["score_bottom"]["score"].tolist() == [-1.0, -2.0, -3.0]
    # 당일 상승폭 최소 = 당일 하락 종목(-2.0)이 1등
    assert v["daychg_low"]["score"].tolist() == [-5.0, -1.0, 2.0]


def test_gate_filter_never_claims_passed_when_columns_missing():
    """게이트 컬럼이 없으면 '통과분'을 주장하지 않는다(빈 집합 = 측정 불가) — 조용한 0건 금지."""
    import pandas as pd
    t = pd.DataFrame({"score": [1.0, 2.0]})
    assert len(er._apply_gate(t)) == 0
    tg = pd.DataFrame({"score": [1.0, 2.0], "gate_ok": [True, False],
                       "r1_ok": [True, True], "heat_ok": [True, True]})
    assert len(er._apply_gate(tg)) == 1
