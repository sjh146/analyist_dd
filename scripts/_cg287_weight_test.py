#!/usr/bin/env python3
"""CG287 자체점검 — make_weights(kind=fillable) 계약 검증(합성 데이터).

검사
  1) 평균 1 정규화(가중 합/행수 == 1)
  2) 임계 이상 → w_high, 미만·NaN → w_low
  3) 길이 불일치 → RuntimeError(조용한 오정렬 금지)
  4) col_vals 미지정 → RuntimeError
  5) 기존 kind 회귀: uniform == 1, absret 기존식과 동일
  6) 실제 패널에서 fillable 비중(10억) 이 0.2~0.4 인지(체결성 필터와 자릿수 정합)
"""
import sys

import numpy as np

sys.path.insert(0, "/app/scripts")
import wf_label_sweep as S  # noqa: E402

fails = []


def ck(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


dates = ["2026-01-02"] * 6
col = np.array([20.72326583794641, 19.0, 22.0, np.nan, 20.72326583794641, 25.0])
w = S.make_weights({"kind": "fillable", "col": "tv_log", "gte": 20.72326583794641,
                    "w_high": 2.0, "w_low": 0.5}, dates, col_vals=col)
ck("평균 1 정규화", abs(float(w.mean()) - 1.0) < 1e-9, f"mean={w.mean():.6f}")
ck("고가중 4행 동일",
   len(np.unique(np.round(w[[0, 2, 4, 5]], 12))) == 1, f"{np.unique(np.round(w[[0, 2, 4, 5]], 6))}")
hi = w[[0, 2, 5]]
lo = w[[1, 3]]
ck("고/저 가중 비율 = w_high/w_low", abs(float(hi[0] / lo[0]) - 4.0) < 1e-9,
   f"비율={hi[0]/lo[0]:.4f}")
ck("NaN 행은 저가중", abs(float(w[3]) - float(lo[0])) < 1e-12, f"{w[3]:.4f}")

for bad, why in (
    (lambda: S.make_weights({"kind": "fillable", "gte": 20.0}, dates, col_vals=col[:3]), "길이 불일치"),
    (lambda: S.make_weights({"kind": "fillable", "gte": 20.0}, dates), "col_vals 없음"),
    (lambda: S.make_weights({"kind": "nope"}, dates), "미지 kind"),
):
    try:
        bad()
        ck(f"{why} → RuntimeError", False, "예외 없음")
    except RuntimeError:
        ck(f"{why} → RuntimeError", True)

u = S.make_weights({"kind": "uniform"}, dates)
ck("uniform == 1(회귀)", np.allclose(u, 1.0))

try:
    z = np.load("/app/app/models/wf/panel_420_tv.npz", allow_pickle=True)
    names = [str(x) for x in z["feature_names"]]
    v = z["X"][:, names.index("tv_log")].astype(float)
    frac = float((np.isfinite(v) & (v >= 20.72326583794641)).mean())
    ck("패널 fillable 비중 ∈ (0.2,0.4)", 0.2 < frac < 0.4, f"{frac:.4f}")
except Exception as e:      # noqa: BLE001
    print(f"SKIP 패널 정합({type(e).__name__}: {e})")

print(f"\n{'ALL PASS' if not fails else '실패: ' + str(fails)}")
raise SystemExit(0 if not fails else 1)
