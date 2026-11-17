#!/usr/bin/env python3
"""CG323 config 자체점검 — 공매도 가산 arm vs 같은 패널 대조군 (컨테이너 안에서 실행).

  docker exec stock_xgboost_ml python /app/scripts/_cg323_config_test.py

검사(전부 PASS 여야 pending 승격 가능):
 ① 두 config 가 CONFIGS 에 존재 ② 패널에 ss_* 4열이 실재 · 시간가변 ③ 두 arm 의 차이가
 exclude_names 뿐(다른 키 전부 동일) ④ exclude_names 접두어가 실제로 4열을 제거 ⑤ 대조군
 제외 후 피처 수 = arm − 4 ⑥ 두 arm 의 (kind/horizon/q/select/core_only) 동일
"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, "/app/scripts")
import wf_label_sweep as W                                   # noqa: E402

PANEL = "/app/app/models/wf/panel_200liq_ffill_2605_ss.npz"
OK, FAIL = [], []


def chk(name, cond, extra=""):
    (OK if cond else FAIL).append(f"{name} {extra}".strip())


def main() -> int:
    by = {c["id"]: c for c in W.CONFIGS}
    chk("① 두 config 존재", "CG323_ctl_h5" in by and "CG323_ss_h5" in by)
    if FAIL:
        print("\n".join(FAIL))
        return 1
    ctl, arm = by["CG323_ctl_h5"], by["CG323_ss_h5"]

    z = np.load(PANEL, allow_pickle=True)
    names = [str(x) for x in z["feature_names"]]
    ss = [n for n in names if n.startswith("ss_")]
    chk("② ss_* 4열 실재", len(ss) == 4, f"({len(ss)}: {ss})")
    X = z["X"]
    for n in ss:
        j = names.index(n)
        std = float(np.nanstd(X[:, j]))
        chk(f"② {n} 시간가변", std > 0, f"std={std:.4f}")

    diff = {k for k in set(ctl) | set(arm) if ctl.get(k) != arm.get(k)}
    chk("③ 차이가 exclude_names 뿐", diff <= {"exclude_names", "id", "desc"}, f"(diff={sorted(diff)})")

    pats = ctl.get("exclude_names") or []
    hit = [n for n in names if any(n == p or (p.endswith("*") and n.startswith(p[:-1])) for p in pats)]
    chk("④ 접두어가 4열 제거", sorted(hit) == sorted(ss), f"(hit={hit})")

    for k in ("kind", "horizon", "q", "select", "core_only"):
        chk(f"⑥ {k} 동일", ctl.get(k) == arm.get(k), f"({ctl.get(k)} vs {arm.get(k)})")

    # ⑤ 패널 열 수 = 217 + 4
    chk("⑤ 패널 열 수 221", X.shape[1] == 221, f"(= {X.shape[1]})")

    print("\n".join(("  PASS " + s) for s in OK))
    if FAIL:
        print("\n".join(("  FAIL " + s) for s in FAIL))
        print(f"{len(OK)}/{len(OK) + len(FAIL)} PASS")
        return 1
    print(f"{len(OK)}/{len(OK)} PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
