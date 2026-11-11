#!/usr/bin/env python3
"""CG192 self-check — '학습에서만 체결 불가 행을 뺀다'는 계약을 검증한다.

검증 항목(전부 PASS 여야 pending 승격 가능):
 1. CONFIGS 에 CG192_mask25_nog_h5 가 있고 core_only=False · train_mask_daychg_gte=0.25.
 2. 같은 런 대조군 CG189_ctl_nog_h5 가 존재하고 **마스크 키를 갖지 않는다**(= no-op 경로).
 3. threshold=None 이면 입력을 그대로 돌려준다(n=0, 인덱스·순서 동일) → 기존 실험 비트 무영향.
 4. 경계 포함: 당일등락 = +0.25 정확히인 행도 제거(>=), +0.249 는 보존.
 5. NaN(종목 첫 행, 폴드 절단면)은 **보존** — '측정 불가'를 '체결 불가'로 오분류하지 않는다.
 6. 인덱스 정렬 유지 — Xtr/ytr 이 tr 순서로 만들어지므로 마스크가 순서를 바꾸면 조용한 오정의.
 7. 실제 패널(panel_200liq_fresh.npz)에서 제거 비율이 0% < p < 30% (arm 이 실제로 무언가를 한다).
 8. 소스 가드: 폴드 루프가 apply_train_domain_mask(tr, ...) 만 호출하고 te 에는 쓰지 않는다.

실행: docker exec stock_xgboost_ml python -u /app/scripts/_cg192_mask_test.py
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = "/app/app/models/wf/panel_200liq_fresh.npz"
ARM, CTL = "CG192_mask25_nog_h5", "CG189_ctl_nog_h5"


def load_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("wls", os.path.join(HERE, "wf_label_sweep.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = load_mod()
    by_id = {c["id"]: c for c in mod.CONFIGS}
    fails = []

    arm = by_id.get(ARM)
    if not arm:
        print(f"FAIL 1: {ARM} 미등록")
        return 1
    if arm.get("core_only") is not False or float(arm.get("train_mask_daychg_gte")) != 0.25:
        fails.append(f"1: arm 파라미터 오류 core_only={arm.get('core_only')} "
                     f"mask={arm.get('train_mask_daychg_gte')}")
    else:
        print(f"PASS 1: {ARM} core_only=False · mask=0.25 · select={arm['select']} "
              f"h={arm['horizon']} q={arm['q']}")

    ctl = by_id.get(CTL)
    if not ctl:
        fails.append(f"2: 대조군 {CTL} 부재(같은 런 짝 Δ 불가)")
    elif ctl.get("train_mask_daychg_gte") is not None:
        fails.append("2: 대조군에 마스크가 걸려 있다(대조 무효)")
    else:
        print(f"PASS 2: 대조군 {CTL} 존재 · 마스크 없음(no-op)")

    df = pd.DataFrame({
        "stock_code": ["A"] * 5 + ["B"] * 3,
        "date": ["d1", "d2", "d3", "d4", "d5", "d1", "d2", "d3"],
        "price": [100.0, 130.0, 136.5, 136.0, 169.9,       # A: +30%, +5%, -0.4%, +24.9%
                  50.0, 62.5, 78.1],                        # B: +25.0% (경계), +25.0%
    })
    out_none, n_none = mod.apply_train_domain_mask(df, None)
    if n_none != 0 or len(out_none) != len(df) or not out_none.index.equals(df.index):
        fails.append(f"3: threshold=None 이 항등이 아니다 n={n_none} len={len(out_none)}")
    else:
        print(f"PASS 3: threshold=None → 항등(n=0, {len(df)}행 유지)")

    out, n = mod.apply_train_domain_mask(df, 0.25)
    kept = list(np.round(np.asarray(out["price"].values, dtype=float), 4))
    # 손계산: A = 100(NaN) · 130(+30%) · 136.5(+5%) · 136.0(-0.37%) · 169.9(+24.93%)
    #         B = 50(NaN) · 62.5(+25.00% 경계) · 78.1(+24.96%)
    # → 제거 2행(A d2 · B d2) · 보존 6행. 경계(+25.00%)는 제거되어야 한다(>=).
    expect_kept = [100.0, 136.5, 136.0, 169.9, 50.0, 78.1]
    if n != 2 or kept != expect_kept:
        fails.append(f"4/5: 제거 결과 오류 n={n} kept={kept} expect={expect_kept}")
    else:
        print(f"PASS 4: 경계 포함(≥0.25 제거) → 제거 {n}행(A+30% · B+25.00%) · 보존 {kept}")
        print("PASS 5: NaN(종목 첫 행) 보존 확인")

    if list(out.index) != sorted(out.index):
        fails.append("6: 인덱스 순서가 뒤바뀜")
    else:
        print(f"PASS 6: 인덱스 정렬 유지 {list(out.index)}")

    if os.path.exists(PANEL):
        z = np.load(PANEL, allow_pickle=True)
        p = pd.DataFrame({"stock_code": z["codes"].astype(str),
                          "date": z["dates"].astype(str),
                          "price": z["price"].astype(float)})
        p = p.sort_values(["stock_code", "date"], kind="mergesort")
        _, n_real = mod.apply_train_domain_mask(p, 0.25)
        frac = n_real / max(1, len(p))
        if not (0.0 < frac < 0.30):
            fails.append(f"7: 실제 패널 제거 비율 이상 {frac:.4f} ({n_real}/{len(p)})")
        else:
            print(f"PASS 7: 실제 패널 마스크 {n_real:,}/{len(p):,}행 = {frac*100:.2f}% 제거")
    else:
        fails.append(f"7: 패널 없음 {PANEL}")

    src = open(os.path.join(HERE, "wf_label_sweep.py"), encoding="utf-8").read()
    ok_call = "apply_train_domain_mask(tr, _mg)" in src
    bad_call = "apply_train_domain_mask(te" in src
    if not ok_call or bad_call:
        fails.append(f"8: 폴드 루프 배선 오류 ok_call={ok_call} bad_call={bad_call}")
    else:
        print("PASS 8: 마스크는 학습행(tr)에만 적용")

    if fails:
        for f in fails:
            print("FAIL", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
