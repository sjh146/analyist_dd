#!/usr/bin/env python3
"""topk_precision.py 자체검증 (CG21 셋업, 2026-09-28).

합성 데이터로 **손으로 계산한 값**과 코드 출력을 대조한다. 이 검증이 없으면 집계기가
'라벨 q 가 다른 두 arm 을 서로 다른 분모 위에서 비교'하는 함정을 조용히 통과시킨다.

기대값(설계):
  후보 6종목 A..F, fwd_ret = +0.10, +0.08, +0.02, -0.02, -0.08, -0.10
  Q05 arm 행: A(y=1), F(y=0)          pred A .9 / F .1
  Q30 arm 행: A(1), B(1), E(0), F(0)  pred A .8 / B .7 / E .3 / F .2
  ① 각자 후보집합 k=2 → Q05 prec 0.5(+0.00), Q30 prec 1.0(+0.0900) → Δprec −0.5
     = 라벨 q 가 다르면 상위 k 지표가 분모 때문에 뒤집힌다(비교 금지의 증거)
  ② --restrict-q 0.05 공통집합 = {A, F} → 두 arm 모두 prec 0.5 → Δprec 0.0000
  ③ 공통집합 크기 2 < k=3 → 측정 짝 없음(포화 감지)
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "topk_precision.py")

RETS = {"A": 0.10, "B": 0.08, "C": 0.02, "D": -0.02, "E": -0.08, "F": -0.10}
Q05 = {"A": (1, 0.9), "F": (0, 0.1)}
Q30 = {"A": (1, 0.8), "B": (1, 0.7), "E": (0, 0.3), "F": (0, 0.2)}


def build(path):
    with open(path, "w") as f:
        for fold in (1, 2):
            for date in ("2026-01-02", "2026-01-03"):
                for exp, rows in (("CO_q05_h5", Q05), ("CO_core30_h5", Q30)):
                    for code, (y, p) in rows.items():
                        f.write(json.dumps({"exp": exp, "fold": fold, "date": date,
                                            "code": code, "y_true": y, "y_pred": p,
                                            "fwd_ret": RETS[code]}) + "\n")


def run(args):
    r = subprocess.run([sys.executable, TOOL] + args, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    tmp = tempfile.mkdtemp(prefix="topk_test_")
    preds = os.path.join(tmp, "preds.jsonl")
    build(preds)
    fails = []

    # ① arm 자체 후보집합 → 분모 차이로 Δprec −0.5
    rc, out = run([preds, "--k", "2", "--arm", "CO_q05_h5", "--control", "CO_core30_h5"])
    if rc != 0 or "Δprec -0.5000" not in out:
        fails.append(f"① 자체 후보집합 Δprec 기대 -0.5000 미일치\n{out[-800:]}")

    # ② 공통 후보집합 → Δprec 0.0000
    rc, out = run([preds, "--k", "2", "--arm", "CO_q05_h5", "--control", "CO_core30_h5",
                   "--restrict-q", "0.05", "--min-pool", "4"])
    if rc != 0 or "Δprec +0.0000" not in out:
        fails.append(f"② 공통집합 Δprec 기대 +0.0000 미일치\n{out[-800:]}")
    if "0.5000" not in out:
        fails.append(f"② 공통집합 prec@2 기대 0.5000 미일치\n{out[-800:]}")

    # ③ 포화: 공통집합(2) < k=3 → 짝 없음
    rc, out = run([preds, "--k", "3", "--arm", "CO_q05_h5", "--control", "CO_core30_h5",
                   "--restrict-q", "0.05", "--min-pool", "4"])
    if "k=3: 측정 짝 없음" not in out:
        fails.append(f"③ 포화 감지 실패\n{out[-800:]}")

    # ④ 동점 제외 부호검정: Δ=0 인 4짝 → 동점 4, p=n/a
    if "동점 4, 부호검정 p=n/a" not in out:
        rc2, out2 = run([preds, "--k", "2", "--arm", "CO_q05_h5",
                         "--control", "CO_core30_h5", "--restrict-q", "0.05", "--min-pool", "4"])
        if "동점 4" not in out2:
            fails.append(f"④ 동점 집계 실패\n{out2[-800:]}")

    # ⑤ JSON 산출물 존재·파싱 (JSON 키는 문자열이 된다)
    jout = os.path.join(tmp, "out.json")
    run([preds, "--k", "2", "--arm", "CO_q05_h5", "--control", "CO_core30_h5",
         "--restrict-q", "0.05", "--min-pool", "4", "--json-out", jout])
    try:
        d = json.load(open(jout))
        assert d["paired"]["2"]["n_dates"] == 4, d["paired"]
        assert d["paired"]["2"]["ties"] == 4, d["paired"]
    except Exception as e:
        fails.append(f"⑤ json 산출물 검증 실패: {type(e).__name__}: {e}")

    if fails:
        print("TOPk TEST FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("TOPk TEST PASS (5/5)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
