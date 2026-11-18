#!/usr/bin/env python3
"""_judge_pair_test — judge_per 의 '같은 런 arm 쌍둥이' 대조군 복원·폴드 짝승률 검증.

실측 배경(2026-11-18 22:20 CG342): `--force-paired` 가 같은 런에 <id>/<id>__ff 두 arm 을
만들었는데, 항목의 counterfactual 문구가 한국어 설명("같은 런·같은 패널…")으로 시작해
첫 토큰('같은')이 config 이름이 아니었다 → 판정기가 대조군을 못 찾아 '기준선없음'(비교 불가)으로
12.7분 실측을 무효 처리했다. 수정: 이름 규칙(<id>__ff ↔ <id>)으로 쌍둥이를 복원한다.

검증 항목:
  1. arm=X__ff · 대조군 문구가 산문 → X 를 대조군으로 복원하고 Δ 를 계산한다(기준선없음 금지)
  2. 같은 런에 X 가 없으면 여전히 '기준선없음'(없는 대조군을 지어내지 않는다)
  3. 대조군 이름이 명시돼 있으면 복원 로직이 판정을 바꾸지 않는다(회귀)
  4. 폴드 길이 불일치/None 에서 죽지 않는다
실행: python3 scripts/_judge_pair_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_engineer_cycle as m  # noqa: E402

FAIL = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAIL.append(name)


def per(**arms):
    out = {}
    for k, v in arms.items():
        mean, folds = v
        out[k] = {"mean": mean, "std": 0.02, "folds": folds,
                  "min": min(folds) if folds else mean, "max": max(folds) if folds else mean,
                  "fold_win_rate": 0.5}
    return out


BASE_F = [0.4877, 0.5482, 0.5491, 0.4923, 0.4992, 0.4739, 0.5339, 0.5531, 0.5029, 0.4873]
FF_F = [0.4756, 0.5485, 0.5435, 0.4942, 0.5018, 0.4702, 0.5361, 0.5528, 0.5034, 0.4873]

print("[1] arm=X__ff · counterfactual 이 산문 → 쌍둥이 복원")
it = {"arm": "LS_quant_q30_h5__ff",
      "counterfactual": "같은 런·같은 패널·같은 폴드의 LS_quant_q30_h5"}
v, d, delta = m.judge_per(it, per(LS_quant_q30_h5=(0.5128, BASE_F), LS_quant_q30_h5__ff=(0.5113, FF_F)))
check("기준선없음 아님", v != "기준선없음")
check(f"Δ −0.0015 계산({delta})", delta is not None and abs(delta + 0.0015) < 1e-6)
check("노이즈(|Δ|<0.02)", v == "노이즈")
check("폴드 짝승률 5/10 표기", "폴드 짝승률 5/10" in d)

print("[2] 쌍둥이가 같은 런에 없으면 기준선없음 유지")
v2, d2, delta2 = m.judge_per({"arm": "X__ff", "counterfactual": "같은 런 설명"},
                             per(X__ff=(0.5113, FF_F)))
check("기준선없음", v2 == "기준선없음")
check("Δ None", delta2 is None)

print("[3] 대조군이 명시된 정상 항목 회귀(복원 로직 무영향)")
v3, d3, delta3 = m.judge_per({"arm": "A", "counterfactual": "B 같은 설명"},
                             per(A=(0.5313, BASE_F), B=(0.5113, FF_F)))
check("Δ +0.0200", delta3 is not None and abs(delta3 - 0.02) < 1e-9)
check("신호있음", v3 == "신호있음")

print("[4] folds 길이 불일치/None 안전")
v4, d4, delta4 = m.judge_per({"arm": "X__ff", "counterfactual": "산문"},
                             per(X=(0.5128, BASE_F[:5]), X__ff=(0.5113, None)))
check("죽지 않고 노이즈", v4 == "노이즈")
check("짝승률 문구 없음(불일치)", "폴드 짝승률" not in d4)

print()
print("PASS" if not FAIL else f"FAIL {len(FAIL)}: {FAIL}")
sys.exit(0 if not FAIL else 1)
