#!/usr/bin/env python3
"""CG182 config self-check — feature-drop arm 이 실제로 3컬럼을 제거하는지 검증한다.

검증 항목(전부 PASS 여야 실행 가능):
 1. CONFIGS 에 CG182_ctl_h5 · CG182_momdrop_h5 가 있고, 둘의 차이는 exclude_names 하나뿐이다
    (= '같은 런·같은 행' 통제가 성립).
 2. 대조군은 exclude_names 를 갖지 않는다(기본 no-op → 비트 동일 경로).
 3. 패널(panel_prod200.npz)의 feature_names 에 제거 대상 3종이 **존재**한다
    (없으면 exclude_names 가 0개를 제거하고 스크립트가 RuntimeError 로 죽는다).
 4. 제거 대상 3종이 패널에서 **상수열이 아니다**(상수면 std>0 필터에 이미 걸러져 제거 효과 0).
 5. 제거 후보 집합이 3개 전부를 포함한다(부분 제거 = 측정 무효).

실행: docker exec stock_xgboost_ml python -u /app/scripts/_cg182_config_test.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PANEL = "/app/app/models/wf/panel_prod200.npz"
DROP = ["momentum_op", "momentum_ni", "momentum_3_12m"]


def main() -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "wls", os.path.join(os.path.dirname(os.path.abspath(__file__)), "wf_label_sweep.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    by_id = {c["id"]: c for c in mod.CONFIGS}
    fails = []

    ctl, arm = by_id.get("CG182_ctl_h5"), by_id.get("CG182_momdrop_h5")
    if not ctl or not arm:
        print("FAIL 1: config 미등록", sorted(k for k in by_id if k.startswith("CG182")))
        return 1
    print("PASS 1: CG182_ctl_h5 · CG182_momdrop_h5 등록")

    if ctl.get("exclude_names"):
        fails.append("2: 대조군에 exclude_names 가 있다")
    else:
        print("PASS 2: 대조군 exclude_names 없음(no-op)")

    diff = {k for k in set(ctl) | set(arm) if k != "desc" and ctl.get(k) != arm.get(k)}
    if diff != {"id", "exclude_names"}:
        fails.append(f"3: 두 arm 의 차이가 exclude_names 만이 아니다 → {sorted(diff)}")
    else:
        print(f"PASS 3: 차이는 id/exclude_names 뿐(desc 제외) → {sorted(diff)}")

    if sorted(arm["exclude_names"]) != sorted(DROP):
        fails.append(f"4: arm 제거 목록 불일치 {arm['exclude_names']}")
    else:
        print(f"PASS 4: 제거 목록 = {DROP}")

    z = np.load(PANEL, allow_pickle=True)
    names = [str(x) for x in z["feature_names"]]
    X = z["X"]
    present = [n for n in DROP if n in names]
    missing = [n for n in DROP if n not in names]
    if missing:
        fails.append(f"5: 패널에 없는 이름 {missing}")
    else:
        print(f"PASS 5: 3종 모두 패널에 존재 (nfeat={len(names)})")

    # 중복 라벨 대비: 이름이 여러 번 나오면 각 열을 모두 검사한다.
    nonconst, const = [], []
    for n in present:
        idx = [j for j, f in enumerate(names) if f == n]
        vals = X[:, idx].astype(float)
        (nonconst if np.nanstd(vals) > 0 else const).append(n)
    if const:
        fails.append(f"6: 상수열(제거 효과 0) {const}")
    else:
        print(f"PASS 6: 전부 시간가변(std>0) → 제거 대상으로 유효 {nonconst}")

    if fails:
        for f in fails:
            print("FAIL", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
