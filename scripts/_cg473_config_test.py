"""CG473 자체점검 — 블렌드 arm 의 '같은 런 짝' 계약을 코드로 고정한다.

왜 필요한가: 이 스택의 실험은 대조군과 **선별·라벨·recipe 가 완전히 같고 학습기만 다른**
경우에만 짝 Δ 로 판정할 수 있다(다르면 인구가 달라 비교가 무효 — CG104/CG470 교훈).
CG473 은 blend_linear 플래그로 학습기만 바꾸므로, 플래그 외의 차이가 0 임을 기계적으로 확인한다.

실행: python3 scripts/_cg473_config_test.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from wf_label_sweep import CONFIGS  # noqa: E402

BY_ID = {c["id"]: c for c in CONFIGS}
CTL = "CO_smooth_d1_h5"
ARMS = ["CG473_blend_h5", "CG473_blend_w3_h5", "CG473_blend_w7_h5"]
PRIMARY = "CG473_blend_h5"

fails = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else ' :: ' + detail}")
    if not ok:
        fails.append(name)


check("대조군 config 존재", CTL in BY_ID, f"{CTL} 없음")
for a in ARMS:
    check(f"{a} 등록", a in BY_ID, "CONFIGS 에 없음")

if not fails:
    ctl = BY_ID[CTL]
    key_fields = ("kind", "horizon", "q", "select", "core_only", "recipe")
    for a in ARMS:
        c = BY_ID[a]
        for f in key_fields:
            check(f"{a}.{f} == 대조군", c.get(f) == ctl.get(f),
                  f"{a}:{c.get(f)!r} vs ctl:{ctl.get(f)!r}")
        check(f"{a} blend_linear=True", c.get("blend_linear") is True, "블렌드 플래그 없음")
        check(f"{a} 대조군과 같은 transform/weight",
              c.get("transform") == ctl.get("transform") and c.get("weight") == ctl.get("weight"),
              "transform/weight 불일치")
    check("사전등록 arm w_lin=0.5", abs(float(BY_ID[PRIMARY].get("blend_w", -1)) - 0.5) < 1e-9,
          str(BY_ID[PRIMARY].get("blend_w")))
    ws = sorted(float(BY_ID[a].get("blend_w")) for a in ARMS)
    check("민감도 w 3종(0.3/0.5/0.7)", ws == [0.3, 0.5, 0.7], str(ws))
    check("대조군 blend_linear 없음", not ctl.get("blend_linear"), "대조군이 블렌드가 됐다")

print(f"\n{'전부 PASS' if not fails else str(len(fails)) + '건 FAIL'}")
sys.exit(0 if not fails else 1)
