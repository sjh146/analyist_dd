#!/usr/bin/env python3
"""회귀 테스트: quant_scoreboard 의 '최고 arm' 은 **파생 출처를 함께 표기**해야 한다.

배경(실측 2026-09-29 22:40): 스코어보드는 원장 전 이력의 모든 arm 중 **최댓값**(max-over-arms)을
북극성으로 쓴다. 그날 최댓값 0.5720(Q5s_30_60)은 **판정이 '노이즈'인** CG24(구간 짝 Δ+0.0176,
양(+) 3/5)에서 나왔는데도 `Δ+0.0321 [신호]` 로 찍혔다 — 엔지니어의 판정과 정면으로 어긋나고,
사람이 '검증된 신호'로 오독한다. 같은 화면에 "7사이클 연속 기준선 대비 +0.02 미달 — 새 레버 필요"
경보가 함께 떠서 자기모순이었다.

계약:
 - 최댓값이 '노이즈'/'악화' 실행에서 나오면 mark 는 [신호] 가 아니라 [미검증 최고 arm]
 - 최댓값이 '신호있음' 실행에서 나오면 종전대로 [신호]
 - 출처 원장 id 가 출력에 남는다(추적 가능)
"""
import importlib.util
import json
import os

SCOREBOARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "scripts", "quant_scoreboard.py")


def _load():
    spec = importlib.util.spec_from_file_location("quant_scoreboard_arm_src", SCOREBOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(tmp_path, recs):
    p = os.path.join(str(tmp_path), "me_ledger.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return p


def _rec(ts, rid, verdict, mean, exp="A"):
    return {"ts": ts, "id": rid, "rc": 0, "verdict": verdict,
            "parsed": {"per_exp": {exp: {"mean": mean, "std": 0.05, "folds": [mean]}}}}


def _state(m, engine):
    """fmt() 는 전체 사슬 상태를 요구한다 — 트레이더/리서처는 스텁으로 채운다(네트워크 접촉 금지)."""
    return {"ts": "2026-09-29T22:40:00+09:00", "goal": "stub",
            "trader": {"role": "trader", "error": "stub", "source": "stub"},
            "engineer": engine,
            "researcher": {"role": "researcher", "status": "ok", "alive_features": 164,
                           "dead_features": 35, "stock_constant_ratio": 0.329,
                           "news_freshness_hours": 0.5, "alerts": []},
            "needs_human": []}


def test_noise_record_best_arm_is_not_labelled_signal(tmp_path):
    # ⚠ CG104(2026-10-05, 리뷰보드 승인) 이후 'Q5s_30_60' 같은 q0.05·게이트ON·슬라이스 arm 은
    # 비교가능성 필터로 best_robust 에서 **제외**된다 → 여기서는 필터를 타지 않는 arm 이름을 써서
    # '출처·표기' 계약만 검증한다(비교가능성 자체는 scripts/_scoreboard_comparability_test.py 담당).
    m = _load()
    base = m.BASELINE_ROBUST
    m.ME_LEDGER = _write(tmp_path, [
        _rec("2026-09-29T03:27:41+09:00", "CG24", "노이즈", base + 0.0314, exp="XX_arm"),
    ])
    st = m.engineer_stanza()
    assert st["best_exp"] == "XX_arm"
    assert st["best_rec_id"] == "CG24"
    assert st["best_validated"] is False
    out = m.fmt(_state(m, st))
    assert "[미검증 최고 arm]" in out
    assert "[신호]" not in out
    assert "출처 CG24" in out


def test_validated_record_best_arm_keeps_signal_mark(tmp_path):
    m = _load()
    base = m.BASELINE_ROBUST
    m.ME_LEDGER = _write(tmp_path, [
        _rec("2026-09-28T19:25:57+09:00", "CG20", "신호있음", base + 0.0236),
    ])
    st = m.engineer_stanza()
    assert st["best_validated"] is True
    assert "[신호]" in m.fmt(_state(m, st))


def test_lower_noise_arm_does_not_shadow_validated_best(tmp_path):
    """최댓값이 검증된 실행에서 나오면, 더 낮은 노이즈 arm 이 있어도 표기는 [신호] 다."""
    m = _load()
    base = m.BASELINE_ROBUST
    m.ME_LEDGER = _write(tmp_path, [
        _rec("2026-09-29T03:27:41+09:00", "CG24", "노이즈", base + 0.0100),
        _rec("2026-09-28T19:25:57+09:00", "CG20", "신호있음", base + 0.0236),
    ])
    st = m.engineer_stanza()
    assert st["best_rec_id"] == "CG20"
    assert st["best_validated"] is True
    assert "[신호]" in m.fmt(_state(m, st))
