#!/usr/bin/env python3
"""_paired_judge_test.py — 구간 짝(paired) 판정 분기 검증 (2026-09-28 CG13 후속).

왜 필요한가: CG13 실측으로 '단일 arm vs 단일 대조군' 판정이 무효가 됐다(같은 크기 서로소 5구간의
폴드 평균이 Δ0.0287 = 사전문턱 +0.02 초과). 구동기 judge_per 에 pairs 분기를 넣었으므로,
①양(+) 짝이 일관되면 신호 ②부호가 뒤섞이면 노이즈 ③악화 ④미측정 짝이 있어도 죽지 않고
⑤**기존 arm/counterfactual 경로가 그대로** 동작하는지를 고정한다.

실행: python3 scripts/_paired_judge_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_engineer_cycle as m  # noqa: E402


def mk(mean):
    return {"mean": mean, "std": 0.02, "min": mean - 0.03, "max": mean + 0.03,
            "folds": [mean] * 5, "fold_win_rate": 0.6}


def run(name, item, per, want_verdict, want_delta=None):
    v, d, delta = m.judge_per(item, per)
    ok = (v == want_verdict) and (want_delta is None or abs((delta or 0) - want_delta) < 1e-9)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: verdict={v} delta={delta} :: {d[:110]}")
    return ok


def main():
    pairs = [[f"A_{i}", f"C_{i}"] for i in range(5)]

    # ① 5구간 모두 양(+) · 평균 +0.024 → 신호
    per = {}
    for i, (a, c) in enumerate(pairs):
        per[a] = mk(0.52 + 0.01 * i)
        per[c] = mk(0.52 + 0.01 * i - 0.024)     # 짝 Δ = +0.024
    r1 = run("일관 양(+) 짝 → 신호", {"pairs": pairs}, per, "신호있음", 0.024)

    # ② 부호 뒤섞임(평균 +0.008) → 노이즈  (CG13 형상: 구간 효과가 짝 안에서 상쇄되지 않음)
    per2 = {}
    for i, (a, c) in enumerate(pairs):
        per2[a] = mk(0.51)
        per2[c] = mk(0.51 - (0.03 if i % 2 == 0 else -0.014))   # Δ = +0.03, -0.014, ...
    r2 = run("부호 뒤섞임 → 노이즈", {"pairs": pairs}, per2, "노이즈")

    # ③ 평균 −0.025 → 악화
    per3 = {}
    for i, (a, c) in enumerate(pairs):
        per3[a] = mk(0.50)
        per3[c] = mk(0.525)
    r3 = run("짝 Δ 평균 −0.025 → 악화", {"pairs": pairs}, per3, "악화", -0.025)

    # ④ 미측정 짝 2개가 있어도 죽지 않고 남은 3구간으로 판정
    per4 = {a: mk(0.53) for a, _ in pairs}
    per4.update({c: mk(0.50) for _, c in pairs[:3]})
    r4 = run("미측정 짝 허용", {"pairs": pairs}, per4, "신호있음", 0.03)

    # ⑤ 기존 arm/counterfactual 경로 회귀(분기 추가가 기존 판정을 바꾸지 않았는지)
    per5 = {"ARM": mk(0.56), "CF": mk(0.53)}
    r5 = run("기존 arm/cf 회귀(Δ+0.03 → 신호)", {"arm": "ARM", "counterfactual": "CF"}, per5,
             "신호있음", 0.03)
    per6 = {"ARM": mk(0.531), "CF": mk(0.53)}
    r6 = run("기존 arm/cf 회귀(Δ+0.001 → 노이즈)", {"arm": "ARM", "counterfactual": "CF"}, per6,
             "노이즈", 0.001)

    ok = all([r1, r2, r3, r4, r5, r6])
    print(f"\n{'전부 PASS' if ok else 'FAIL 있음'} ({sum([r1, r2, r3, r4, r5, r6])}/6)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
