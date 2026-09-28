#!/usr/bin/env python3
"""_sample_weight_test.py — CG23(표본 가중 축) 배관 회귀 테스트.

컨테이너 안에서 실행:  docker exec stock_xgboost_ml python /app/scripts/_sample_weight_test.py

검증 (PASS/FAIL 을 찍고 실패 시 rc=1):
  T1  make_weights 정적 성질 — None→None · uniform→전부 1 · time_decay hl60 단조/비율 유한
  T2  w=None 경로가 원본 ml.train_seed 와 **비트 동일**(무가중 경로 무변경 증명)
  T3  균등 가중(WDu) == 무가중(WDn) 비트 동일 — '가중 배관'이 AUC 를 바꾸지 않는다
  T4  시간 감쇠(WDw) != 균등 가중 — 가중치가 실제로 모델에 도달한다(Δ 귀속 가능성)

T3 이 깨지면(배관 자체가 AUC 를 흔들면) WDw 의 Δ 를 가중 효과로 귀속할 수 없다 — 그래서 검정한다.
"""
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_label_sweep as LS  # noqa: E402

FAIL = []
PANEL = "/app/app/models/wf/panel_150u.npz"
PY = sys.executable or "python"


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)
    if not ok:
        FAIL.append(name)


# ── T1: make_weights 정적 성질 ────────────────────────────────────────────────
def t1():
    print("T1 make_weights 정적 성질", flush=True)
    dates = ["2026-01-0%d" % d for d in range(1, 6)]          # 5 거래일
    day_pos = {d: i for i, d in enumerate(dates)}
    check("spec=None → None", LS.make_weights(None, dates) is None)
    wu = LS.make_weights({"kind": "uniform"}, dates)
    check("uniform → 전부 1.0", np.allclose(wu, 1.0), f"mean={wu.mean():.6f}")
    wd = LS.make_weights({"kind": "time_decay", "hl": 60}, dates,
                         day_pos=day_pos, t0_pos=4)
    check("time_decay 평균 1 정규화", abs(float(wd.mean()) - 1.0) < 1e-12,
          f"mean={wd.mean():.6f}")
    check("time_decay 최신 행이 최대", abs(float(wd[-1]) - float(wd.max())) < 1e-12,
          f"w[-1]={wd[-1]:.4f} max={wd.max():.4f}")
    check("time_decay 단조 증가(시간순)", bool(np.all(np.diff(wd) > 0)),
          f"w={np.round(wd, 4).tolist()}")
    # hl60 · 경과 4일 → 가장 오래된 행의 가중은 0.5**(4/60) 배(정규화 전 이론값)
    ratio = float(wd[-1] / wd[0])
    expect = 0.5 ** (-4 / 60.0)
    check("time_decay hl60 이론 비율", abs(ratio - expect) < 1e-12,
          f"ratio={ratio:.6f} expect={expect:.6f}")
    wa = LS.make_weights({"kind": "absret", "clip": 0.05}, dates,
                         fwd_tr=np.array([0.01, -0.02, 0.05, 0.0, 0.03]))
    check("absret 평균 1 · 완전 0 가중 없음", abs(float(wa.mean()) - 1.0) < 1e-12
          and float(wa.min()) > 0.0, f"min={wa.min():.4f}")


# ── T2: w=None 경로 == 원본 train_seed ────────────────────────────────────────
def t2():
    print("T2 w=None 경로가 원본 ml.train_seed 와 비트 동일", flush=True)
    import train_curated as tc
    tc.select_curated_features = lambda n, a=False: list(n)
    rng = np.random.default_rng(7)
    n, p = 400, 6
    Xtr = rng.normal(size=(n, p)).astype(np.float32)
    ytr = (rng.random(n) < 0.35).astype(int)
    Xte = rng.normal(size=(150, p)).astype(np.float32)
    yte = (rng.random(150) < 0.35).astype(int)
    names = [f"f{i}" for i in range(p)]
    a1 = LS.ml.train_seed(Xtr, None, Xte, ytr, None, yte, names,
                          "/tmp/_sw_test_m1", 3, 0.05, 2, 50, True, None)[0]
    a2 = LS.train_seed_weighted(Xtr, None, Xte, ytr, None, yte, names,
                                "/tmp/_sw_test_m2", 3, 0.05, 2, 50, True, None, None)[0]
    check("AUC 비트 동일", float(a1) == float(a2), f"orig={a1:.10f} new={a2:.10f}")


# ── T3/T4: 실제 패널에서 무가중 vs 균등 vs 시간감쇠 ────────────────────────────
def run_sweep(only, tag, folds=3, seeds=2):
    out = f"/tmp/_sw_{tag}.jsonl"
    summ = f"/tmp/_sw_{tag}.json"
    cmd = [PY, "-u", "/app/scripts/wf_label_sweep.py", "--panel", PANEL,
           "--folds", str(folds), "--seeds", str(seeds), "--only", only,
           "--out", out, "--summary-out", summ]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd="/app")
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:], flush=True)
        raise RuntimeError(f"sweep 실패 rc={r.returncode} ({only})")
    recs = [json.loads(l) for l in open(out)]
    rec = [r_ for r_ in recs if r_["exp"] == only][-1]
    if rec.get("status") != "ok":
        raise RuntimeError(f"{only} status={rec.get('status')} err={rec.get('error')}")
    return rec


def t3_t4():
    if not os.path.exists(PANEL):
        print(f"  [SKIP] T3/T4 — 패널 없음: {PANEL}", flush=True)
        return
    print("T3/T4 실제 패널: 무가중(WDn) vs 균등(WDu) vs 시간감쇠(WDw hl60)", flush=True)
    rn = run_sweep("WDn_00_30", "n")
    ru = run_sweep("WDu_00_30", "u")
    rw = run_sweep("WDw_h60_00_30", "w")
    dn = {k: v["mean"] for k, v in rn["folds"].items()}
    du = {k: v["mean"] for k, v in ru["folds"].items()}
    dw = {k: v["mean"] for k, v in rw["folds"].items()}
    print(f"    WDn {dn}", flush=True)
    print(f"    WDu {du}", flush=True)
    print(f"    WDw {dw}", flush=True)
    dmax = max(abs(dn[k] - du[k]) for k in dn)
    check("T3 균등 가중 == 무가중 (폴드별 비트 동일)", dmax == 0.0, f"max|Δ|={dmax:.12f}")
    wd = max(abs(dw[k] - du[k]) for k in dw)
    check("T4 시간감쇠 != 균등 (가중이 모델에 도달)", wd > 0.0, f"max|Δ|={wd:.6f}")
    ws = rw["folds"][sorted(rw["folds"])[0]].get("weight_stats")
    check("T4 가중 통계 기록(유니크 > 1)", bool(ws) and ws["n_unique"] > 1, f"stats={ws}")
    print(f"    참고(판정 아님): WDw−WDn 폴드 평균 Δ = "
          f"{np.mean([dw[k]-dn[k] for k in dn]):+.4f}", flush=True)


if __name__ == "__main__":
    t1()
    t2()
    try:
        t3_t4()
    except Exception as e:  # noqa: BLE001
        check("T3/T4 실행", False, f"{type(e).__name__}: {e}")
    print(f"\n=== 결과: {'전부 PASS' if not FAIL else 'FAIL ' + str(FAIL)} ===", flush=True)
    sys.exit(1 if FAIL else 0)
