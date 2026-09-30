#!/usr/bin/env python3
"""judge_champion_baseline 의 짝(counterfactual_value) 경로 자체점검.

왜: 챌린저를 챔피언과 **같은 champion_robust_eval 프로토콜**로 잰 항목(CG33)은 Δ 를 자동 판정해야
하는데, 종전 judge 는 baseline.value 에 '기존 승격 기준선 x(단일분할)' 문구를 붙여 **거짓 보고**를
만든다(0.5163 은 단일분할 값이 아니라 같은 프로토콜의 OOS 실측이다). 대조값이 명시된 항목만 짝
경로로 보내고, 그 밖의 항목은 종전 동작(회귀)을 유지하는지 확인한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

FAIL = 0


def check(name, cond, got=""):
    global FAIL
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — got={got}"))
    if not cond:
        FAIL += 1


PARSED = {
    "robust_auc": 0.5150,
    "auc_std_across_folds": 0.0340,
    "fold_means": [0.4691, 0.5320, 0.5479],
    "auc_pooled": 0.519,
    "auc_per_date_mean": 0.5163,
    "rows_scored": 1800,
}

# 1) 짝 신호: 챌린저가 대조 챔피언(0.5163) 대비 +0.0237 → 문턱 +0.02 초과
P1 = dict(PARSED, robust_auc=0.5400)
v, d, delta = m.judge_champion_baseline(
    {"counterfactual_value": 0.5163, "counterfactual": "배포 챔피언(CG45 0.5163)"}, P1)
check("짝 신호 verdict", v == "짝 신호", v)
check("짝 신호 delta", abs(delta - (0.5400 - 0.5163)) < 1e-9, delta)
check("짝 신호 문구에 대조값", "0.5163" in d, d)
check("짝 경로는 '단일분할' 문구를 붙이지 않는다", "단일분할" not in d, d)

# 2) 짝 노이즈: Δ +0.010 < +0.02
P2 = dict(PARSED, robust_auc=0.5263)
v2, d2, delta2 = m.judge_champion_baseline({"counterfactual_value": 0.5163}, P2)
check("짝 노이즈 verdict", v2 == "짝 노이즈", v2)
check("짝 노이즈 delta", abs(delta2 - 0.0100) < 1e-9, delta2)

# 3) 짝 악화도 노이즈로(문턱은 양(+) 방향만 신호)
v3, _, delta3 = m.judge_champion_baseline({"counterfactual_value": 0.5163}, dict(PARSED, robust_auc=0.4800))
check("짝 악화 verdict=짝 노이즈", v3 == "짝 노이즈", v3)
check("짝 악화 delta 부호", delta3 < 0, delta3)

# 4) 회귀: counterfactual_value 가 없으면 종전 경로(기준선 실측 + 단일분할 문구)
v4, d4, delta4 = m.judge_champion_baseline({"baseline": {"value": 0.5513}}, PARSED)
check("회귀 verdict=기준선 실측", v4 == "기준선 실측", v4)
check("회귀 delta None", delta4 is None, delta4)
check("회귀 단일분할 문구 유지", "0.5513" in d4 and "단일분할" in d4, d4)

# 5) 회귀: baseline 이 없는 기준선 실측 항목도 그대로
v5, d5, _ = m.judge_champion_baseline({}, PARSED)
check("회귀 baseline 없는 항목", v5 == "기준선 실측", v5)

# 6) robust_auc 없으면 판정불가(대조값이 있어도)
v6, d6, _ = m.judge_champion_baseline({"counterfactual_value": 0.5163}, {"error": "요약 파일 없음"})
check("요약 없음 → 판정불가", v6 == "판정불가", v6)
check("요약 없음 detail=error", d6 == "요약 파일 없음", d6)

print(f"\n{'ALL PASS' if not FAIL else str(FAIL) + ' FAIL'}")
sys.exit(1 if FAIL else 0)
