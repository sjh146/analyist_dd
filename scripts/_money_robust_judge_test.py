#!/usr/bin/env python3
"""_money_robust_judge_test.py — 돈 판정기의 꼬리 강건성 병기(CG499) 자체점검.

WHY: CG495 는 Δ+2.622(t2.61)·분할 both_positive 만으로 '신호있음'을 찍었고, CG496 이
상위3세션을 빼자 t 1.69 로 무너지는 것을 보였다. 판정 **문턱은 그대로** 두고, 판정 문구에
부호검정 p 와 '상위3세션 제거 t' 를 자동 병기해 같은 오독이 반복되지 않게 한다.

회귀 계약: session_deltas 가 없는 구 스키마 요약은 verdict 문자열이 비트 동일('신호있음')해야 한다.

pytest 없는 스택이므로 PASS/FAIL 을 직접 출력하고 실패 시 rc=1.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m   # noqa: E402
import fillable_topk_expectancy as fte   # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    ok = bool(cond)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))
    if not ok:
        FAIL += 1


def block(delta=0.25, t=2.6, net=0.60, stable="both_positive", session_deltas=None,
          pos=44, neg=30):
    paired = {"n_dates": 74, "delta_mean": delta, "median": delta, "sd": 1.0, "t": t,
              "pos_pct": 55.0, "pos": pos, "neg": neg, "ties": 0,
              "folds": {"1": "5/8", "2": "4/8"}}
    if session_deltas is not None:
        paired["session_deltas"] = session_deltas
    return {"arm": {"n": 74, "mean": net, "median": net, "sd": 1.2, "t": 2.5,
                    "pos_pct": 55.0, "worst": -4.0, "best": 5.0},
            "control": {"n": 74, "mean": net - delta, "median": net - delta, "sd": 1.2,
                        "t": 1.0, "pos_pct": 50.0, "worst": -4.5, "best": 4.8},
            "arm_halves": {"front": {"n": 37, "mean": 0.3}, "back": {"n": 37, "mean": 0.7},
                           "stable": stable, "split_session": "(5, '2026-03-02')"},
            "paired": paired}


def summary(blocks):
    return {"metric_name": "fillable_topk_expectancy", "exit": "close_h", "horizon": 5,
            "ks": [3, 5], "arm_tag": "a", "control_tag": "b", "fee_roundtrip_pct": 0.21,
            "rows": {"arm": 1, "control": 1}, "n_sessions_fillable": 125,
            "pool_median_fillable": 48, "stride": 5,
            "conditions": {"fillable": {"filtered_out": {"arm": 0, "control": 0},
                                        "k": {"3": blocks[0], "5": blocks[1]}}}}


TAIL = {f"2026-01-{i + 1:02d}": (0.05 if i < 11 else 9.0) for i in range(12)}
FLAT = {f"2026-01-{i + 1:02d}": 0.25 + 0.01 * i for i in range(12)}


def judge(payload):
    tmp = tempfile.mkdtemp(prefix="mrj_")
    p = os.path.join(tmp, "s.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    parsed = m.parse_fillable_topk_expectancy(p, 0.0)
    return m.judge_fillable_topk_expectancy({"id": "CG499", "metric": "fillable_topk_expectancy"}, parsed)


def main():
    v, d, _ = judge(summary([block(), block()]))
    check("구 스키마: verdict 비트 동일", v == "신호있음", v)
    check("구 스키마: '상위3세션 제거 미실시' 명시", "상위3세션 제거 미실시" in d, d[:160])
    v, d, _ = judge(summary([block(session_deltas=TAIL), block(session_deltas=TAIL)]))
    check("꼬리 의존: '⚠꼬리검사 미달' 접미", v == "신호있음 ⚠꼬리검사 미달", v)
    check("꼬리 의존: 부호검정 p 병기", "부호검정 p=" in d)
    check("꼬리 의존: 상위3세션 제거 병기", "상위3세션 제거 Δ" in d)
    v, d, _ = judge(summary([block(session_deltas=FLAT, pos=30, neg=6),
                             block(session_deltas=FLAT, pos=30, neg=6)]))
    check("강건: 접미 없음", v == "신호있음", v)
    p1 = m._binom_p_two_sided(18, 9)
    check("부호검정 18/9 → p≈0.122", p1 is not None and abs(p1 - 0.1223) < 0.01, str(p1))
    p2 = m._binom_p_two_sided(24, 3)
    check("부호검정 24/3 → p<0.001", p2 is not None and p2 < 0.001, str(p2))
    check("부호검정 표본 8 미만 → None", m._binom_p_two_sided(3, 2) is None)
    arm = {("f1", "d1"): 1.0, ("f1", "d2"): 3.0, ("f2", "d1"): 3.0}
    ctl = {("f1", "d1"): 0.0, ("f1", "d2"): 1.0, ("f2", "d1"): 1.0}
    s = fte.paired_stats(arm, ctl)
    check("paired_stats: session_deltas 존재", isinstance(s.get("session_deltas"), dict), str(s.get("session_deltas")))
    check("paired_stats: d1 = 폴드평균 Δ1.5", abs(s["session_deltas"]["d1"] - 1.5) < 1e-9,
          str(s.get("session_deltas")))
    check("paired_stats: 기존 집계 불변(n_dates=3)", s.get("n_dates") == 3, str(s.get("n_dates")))

    print(f"\n{'ALL PASS' if not FAIL else str(FAIL) + ' FAIL'}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
