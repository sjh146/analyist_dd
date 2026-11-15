#!/usr/bin/env python3
"""CG286 자체점검 — apply_train_mask_col 계약 검증(합성 데이터, DB·패널 비의존).

검사 항목
  1) col/threshold 미지정 → 입력과 **비트 동일**(기존 실험 무영향)
  2) 임계 미만 행만 제거되고 임계 **경계값은 보존**(>=)
  3) NaN(측정 불가) 행은 제거된다 — '측정 불가'는 학습에서 뺀다(마스크 정의)
  4) 인덱스가 보존된다(원 프레임 인덱스 유지 — 이후 tr 재사용이 위치 기반이므로 중요)
  5) 컬럼 없음 → RuntimeError(조용한 통과 금지)
  6) 실제 패널(panel_420_tv)에서 임계별 제거율이 CG283 의 체결성 필터와 정합적인가
     (10억 임계에서 살아남는 비율이 '체결가능 36%' 근처인지 — 자릿수 확인용)
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app/scripts")
import wf_label_sweep as S  # noqa: E402

fails = []


def ck(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


def mk():
    return pd.DataFrame({
        "stock_code": ["A"] * 4 + ["B"] * 4,
        "date": ["2026-01-0%d" % (i + 1) for i in range(4)] * 2,
        "price": [100.0, 101.0, 99.0, 100.0, 10.0, 10.5, 10.0, 11.0],
        "tv_log": [20.0, 20.723265837945, 21.5, np.nan, 19.0, 22.0, 20.723265837945, 23.0],
        "f1": list(range(8)),
    })


base = mk()
tr, n = S.apply_train_mask_col(base, None, None)
ck("미지정 → 원본 비트 동일", n == 0 and tr.equals(base))

tr2, n2 = S.apply_train_mask_col(base, "tv_log", 20.723265837945)
ck("경계값 보존(>=)", float(tr2["tv_log"].min()) >= 20.723265837945, f"min={tr2['tv_log'].min()}")
ck("임계 미만·NaN 제거 수", n2 == 3, f"n={n2} (20.0·19.0·NaN)")
ck("남은 행 수", len(tr2) == 5, f"{len(tr2)}")
ck("인덱스 보존", list(tr2.index) == [1, 2, 5, 6, 7], f"{list(tr2.index)}")

try:
    S.apply_train_mask_col(base, "nope", 1.0)
    ck("없는 컬럼 → RuntimeError", False, "예외 없음")
except RuntimeError:
    ck("없는 컬럼 → RuntimeError", True)

# 6) 실제 패널 정합(있으면)
try:
    z = np.load("/app/app/models/wf/panel_420_tv.npz", allow_pickle=True)
    names = [str(x) for x in z["feature_names"]]
    v = z["X"][:, names.index("tv_log")].astype(float)
    for thr, label in ((20.72326583794641, "10억"), (21.821878125947855, "30억")):
        frac = float((np.isfinite(v) & (v >= thr)).mean())
        print(f"INFO panel_420_tv 생존율 {label} 임계: {frac:.4f}")
    ck("10억 임계 생존율 ∈ (0.2,0.6)",
       0.2 < float((np.isfinite(v) & (v >= 20.72326583794641)).mean()) < 0.6)
except Exception as e:      # noqa: BLE001
    print(f"SKIP 패널 정합({type(e).__name__}: {e})")

print(f"\n{6 - len(fails)}/6 PASS" if not fails else f"\n실패: {fails}")
raise SystemExit(0 if not fails else 1)
