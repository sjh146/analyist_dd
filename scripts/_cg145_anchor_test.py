#!/usr/bin/env python3
"""CG145 회귀 테스트 — robust_paired_seeds.py 의 앵커 키 모드.

왜: CG145(앵커 평균 프로토콜) 구현은 **기존 시드 모드 결과를 1비트도 바꾸면 안 되고**,
새 앵커 모드는 앵커별 짝 Δ 의 평균±std 와 '앵커 잡음 하한(mean−sd)'을 기록해야 한다.
실측 근거(CG145): 같은 모델·config 에서 앵커만 옮겨도 Δ 가 +0.0055/+0.0352/+0.0265 로 갈려
2/3 앵커만 문턱을 넘었다 → 단일 앵커 판정은 앵커 선택의 산물일 수 있다.

실행: python3 scripts/_cg145_anchor_test.py   (전부 PASS 면 exit 0)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "robust_paired_seeds.py")

FAILS = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def write_eval(path, *, seed=0, anchor=None, auc, folds, windows):
    payload = {
        "universe": {"seed": seed},
        "window_anchor": anchor,
        "robust_auc": auc,
        "auc_per_date_mean": auc,
        "folds": [{"fold": i + 1, "window": w, "auc_mean": a} for i, (w, a) in enumerate(zip(windows, folds))],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def run(args):
    out = os.path.join(tempfile.mkdtemp(), "paired.json")
    r = subprocess.run([sys.executable, SCRIPT, *args, "--out", out],
                       capture_output=True, text=True)
    res = None
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            res = json.load(f)
    return r.returncode, res, r.stdout + r.stderr


def main():
    d = tempfile.mkdtemp()

    a1 = [os.path.join(d, f"a_s{i}.json") for i in range(3)]
    b1 = [os.path.join(d, f"b_s{i}.json") for i in range(3)]
    for i, (aa, bb) in enumerate([(0.55, 0.54), (0.56, 0.55), (0.54, 0.53)]):
        write_eval(a1[i], seed=i, auc=aa, folds=[0.55], windows=[["2026-11-01", "2026-11-10"]])
        write_eval(b1[i], seed=i, auc=bb, folds=[0.54], windows=[["2026-11-01", "2026-11-10"]])
    rc, res, log = run(["--arm-a", *a1, "--arm-b", *b1, "--label-a", "A", "--label-b", "B"])
    check("1a 시드 모드 rc=0", rc == 0, log[-300:])
    check("1b 시드 모드 key_by=seed·anchor_noise=None",
          res and res.get("key_by") == "seed" and res.get("anchor_noise") is None)
    check("1c 시드 모드 Δ 평균 0.01",
          res and abs(res["delta_mean"] - 0.01) < 1e-6, str(res and res.get("delta_mean")))
    check("1d 시드 모드 부호 3/3·문턱 미달(0.02)",
          res and res["pos_seeds"] == 3 and res["meets_threshold"] is False)
    check("1e 창 서명 동일 → window_mismatch 없음", res and res["window_mismatch"] == [])

    anchors = ["2026-11-06", "2026-11-13", "2026-11-20"]
    deltas = {"2026-11-06": 0.0055, "2026-11-13": 0.0352, "2026-11-20": 0.0265}
    base = 0.5100
    a2 = [os.path.join(d, f"a_anc_{x}.json") for x in anchors]
    b2 = [os.path.join(d, f"b_anc_{x}.json") for x in anchors]
    for i, x in enumerate(anchors):
        write_eval(a2[i], anchor=x, auc=base + deltas[x], folds=[base + 0.005, base],
                   windows=[["2026-10-02", "2026-10-15"], ["2026-10-16", "2026-10-30"]])
        write_eval(b2[i], anchor=x, auc=base, folds=[base, base + 0.01],
                   windows=[["2026-10-02", "2026-10-15"], ["2026-10-16", "2026-10-30"]])
    rc, res, log = run(["--arm-a", *a2, "--arm-b", *b2, "--key-by", "anchor"])
    check("2a 앵커 모드 rc=0", rc == 0, log[-300:])
    check("2b 공통 앵커 3개를 날짜순으로", res and res["seeds"] == anchors, str(res and res.get("seeds")))
    check("2c 앵커별 Δ 가 window_anchor 로 짝지어짐",
          res and all(abs(res["deltas"][x] - deltas[x]) < 1e-6 for x in anchors), str(res and res.get("deltas")))
    check("2d 짝 Δ 평균 = +0.0224 (CG145 재현)",
          res and abs(res["delta_mean"] - 0.0224) < 5e-4, str(res and res.get("delta_mean")))
    check("2e 앵커 산포 기록(sd>0)", res and res["anchor_noise"] and res["anchor_noise"]["delta_sd"] > 0)
    check("2f 평균 문턱 통과(meets_threshold=True)",
          res and res["meets_threshold"] is True)
    check("2g 앵커 잡음 하한(mean−sd)은 문턱 미달 → gate False",
          res and res["anchor_noise"]["mean_minus_sd"] < 0.02
          and res["anchor_noise"]["gate_mean_minus_sd_ge_threshold"] is False,
          str(res and res.get("anchor_noise")))

    b3 = [os.path.join(d, f"b_badwin_{x}.json") for x in anchors]
    for i, x in enumerate(anchors):
        write_eval(b3[i], anchor=x, auc=base - 0.05, folds=[base, base],
                   windows=[["2026-10-09", "2026-10-22"], ["2026-10-23", "2026-11-06"]])
    rc, res, log = run(["--arm-a", *a2, "--arm-b", *b3, "--key-by", "anchor"])
    check("3a 창 불일치 검출", rc == 0 and len(res["window_mismatch"]) == 3, str(res and res.get("window_mismatch")))
    check("3b 창 불일치면 문턱 통과라도 meets_threshold=False", res and res["meets_threshold"] is False)

    a4 = [os.path.join(d, f"a_noanc_{i}.json") for i in range(2)]
    b4 = [os.path.join(d, f"b_noanc_{i}.json") for i in range(2)]
    for i in range(2):
        write_eval(a4[i], seed=i, auc=base, folds=[base], windows=[["2026-11-01", "2026-11-10"]])
        write_eval(b4[i], seed=i, auc=base + 0.03, folds=[base], windows=[["2026-11-01", "2026-11-10"]])
    rc, res, log = run(["--arm-a", *a4, "--arm-b", *b4, "--key-by", "anchor"])
    check("4a window_anchor 없으면 공통 키 1개 → rc=2 판정불가", rc == 2 and res and "error" in res, str(res))

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}/{len(FAILS) + 10}: {FAILS}")
        return 1
    print("전부 PASS — 시드 모드 불변 · 앵커 키 짝짓기 · 앵커 잡음 하한 · 창 불일치 차단")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
