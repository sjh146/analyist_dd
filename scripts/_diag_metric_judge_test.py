#!/usr/bin/env python3
"""judge_per 회귀 — baseline.value=null(진단·계측기 항목)에서 float(None) 크래시 방지.

실측(2026-10-03 21:1x): CG81(진단 항목: baseline.value=null + arm 지정)을 --ingest 하자
`TypeError: float() argument must be a string or a real number, not 'NoneType'` 로 구동기가 죽었다.
원장 기록은 그 뒤 단계라 남지 않는다 → 항목이 흔적 없이 사라진다(설계원칙 4 위반).
수리: baseline.value 가 숫자가 아니면 '기준선없음'으로 정직하게 끝낸다(예외 금지).

호스트에서 실행: python3 scripts/_diag_metric_judge_test.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import model_engineer_cycle as m  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" · {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


PER = {
    "CO_core30_h5": {"mean": 0.5315, "folds": [0.52, 0.53], "std": 0.02},
    "CO_smooth_d1_h5": {"mean": 0.5355, "folds": [0.53, 0.54], "std": 0.02},
}

print("[1] 진단 항목 — baseline.value=None + arm (종전 크래시 경로)")
item = {
    "id": "CG81",
    "arm": "CO_core30_h5",
    "baseline": {"value": None, "source": "동전(0.5) + MT116 라이브 max 0.4733"},
    "counterfactual": "동전(0.5) · 전체 양성률 0.50 · MT116 라이브 max 0.4733",
}
try:
    v, det, delta = m.judge_per(item, PER)
    check("예외 없음", True)
    check("판정 = 기준선없음", v == "기준선없음", f"verdict={v}")
    check("사유에 '값 없음'", "값 없음" in det, det[:90])
    check("delta None", delta is None, f"delta={delta}")
except Exception as e:
    check("예외 없음", False, f"{type(e).__name__}: {e}")

print("[2] 회귀 — arm + 대조군이 같은 런에 있고 둘 다 측정됨")
item2 = {"id": "X", "arm": "CO_smooth_d1_h5", "baseline": {"value": None},
         "counterfactual": "CO_core30_h5 (같은 런)"}
try:
    v, det, delta = m.judge_per(item2, PER)
    check("Δ 계산", delta == 0.004, f"delta={delta}")
    check("판정 노이즈", v == "노이즈", f"verdict={v}")
except Exception as e:
    check("예외 없음", False, f"{type(e).__name__}: {e}")

print("[3] 회귀 — arm + 기록 기준선(숫자)")
item3 = {"id": "Y", "arm": "CO_core30_h5", "baseline": {"value": 0.5406, "source": "등록 기준선"},
         "counterfactual": "CO_other_h5 (이 런에 없음)"}
try:
    v, det, delta = m.judge_per(item3, PER)
    check("Δ = arm − 기준선", delta == round(0.5315 - 0.5406, 4), f"delta={delta}")
    check("판정 노이즈", v == "노이즈", f"verdict={v}")
    check("기록 기준선 문구", "기록 기준선" in det, det[:90])
except Exception as e:
    check("예외 없음", False, f"{type(e).__name__}: {e}")

print("[4] 회귀 — baseline 자체가 없음")
try:
    v, det, delta = m.judge_per({"id": "Z", "arm": "CO_core30_h5", "counterfactual": "CO_x"}, PER)
    check("판정 = 기준선없음", v == "기준선없음", f"verdict={v}")
except Exception as e:
    check("예외 없음", False, f"{type(e).__name__}: {e}")

print("[5] 회귀 — 구간 짝(pairs) 경로")
item5 = {
    "id": "P", "counterfactual": "ctl",
    "pairs": [["a1", "c1"], ["a2", "c2"]],
    "baseline": {"value": None},
}
per5 = {"a1": {"mean": 0.54}, "c1": {"mean": 0.52}, "a2": {"mean": 0.53}, "c2": {"mean": 0.52}}
try:
    v, det, delta = m.judge_per(item5, per5)
    check("Δ 계산", delta == round((0.02 + 0.01) / 2, 4), f"delta={delta}")
    check("판정 노이즈", v == "노이즈", f"verdict={v}")
except Exception as e:
    check("예외 없음", False, f"{type(e).__name__}: {e}")

print()
if FAIL:
    print(f"FAIL {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("ALL PASS")
