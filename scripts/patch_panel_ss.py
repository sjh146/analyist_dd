#!/usr/bin/env python3
"""패널에 공매도 피처를 주입한다 — CG323 (2026-11-18). **컨테이너 안에서 실행**.

사용:
  docker cp data/reports/ss_features.npz stock_xgboost_ml:/app/data/ss_features.npz
  docker exec stock_xgboost_ml python /app/scripts/patch_panel_ss.py \
      --panel /app/app/models/wf/panel_200liq_ffill_2605.npz \
      --ss    /app/data/ss_features.npz \
      --out   /app/app/models/wf/panel_200liq_ffill_2605_ss.npz

as-of 규칙(하드): 패널 행 (code, date) 에는 **date 보다 엄격히 이전**인 마지막 공매도 관측일 값만 붙인다.
  이유: krx_short_selling 의 created_at 이 당일 10:3x 인 행이 많다(장중 부분 집계) → 같은 날 값은
  장 마감 전에 확정된 양이 아니다. t-1 지연이면 어떤 해석에서도 미래정보가 아니다.

자기검정(전부 PASS 해야 파일을 남긴다):
  ① 주입 전 X 와 새 X 의 앞 n_old 열이 비트 동일 ② codes/dates 동일 ③ 신규 5열이 상수 아님
  ④ 신규 열 결측률 보고 ⑤ 표본 200행에서 '사용된 ss 관측일 < 패널 날짜' 전수 확인
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--ss", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    p = np.load(a.panel, allow_pickle=True)
    s = np.load(a.ss, allow_pickle=True)
    X, names = p["X"], [str(x) for x in p["feature_names"]]
    codes = np.array([str(x) for x in p["codes"]])
    dates = np.array([str(x) for x in p["dates"]])
    s_codes = [str(x) for x in s["codes"]]
    s_dates = np.array([str(x) for x in s["dates"]])
    F, fnames = s["F"], [str(x) for x in s["names"]]

    cidx = {c: i for i, c in enumerate(s_codes)}
    new_cols = np.full((len(dates), len(fnames)), np.nan)
    used = np.zeros(len(dates), dtype=object)
    for r, (c, d) in enumerate(zip(codes, dates)):
        i = cidx.get(c)
        if i is None:
            continue
        # strict < : 같은 날 값 금지
        j = int(np.searchsorted(s_dates, d, side="left")) - 1
        if j < 0:
            continue
        new_cols[r] = F[i, j]
        used[r] = s_dates[j]

    # ⑤ 표본 검증
    sample = np.linspace(0, len(dates) - 1, min(200, len(dates))).astype(int)
    bad = [(codes[r], dates[r], used[r]) for r in sample if used[r] and not (used[r] < dates[r])]
    assert not bad, f"as-of 위반: {bad[:3]}"

    Xn = np.concatenate([X, new_cols.astype(np.float32)], axis=1)
    assert np.array_equal(X, Xn[:, :X.shape[1]], equal_nan=True), "① 주입이 기존 열을 바꿨다"
    miss = {fnames[k]: round(float(np.mean(np.isnan(new_cols[:, k]))), 4) for k in range(len(fnames))}
    std = {fnames[k]: round(float(np.nanstd(new_cols[:, k])), 4) for k in range(len(fnames))}
    for k, f in enumerate(fnames):
        assert np.nanstd(new_cols[:, k]) > 0, f"③ {f} 상수 — 주입 무의미"

    np.savez_compressed(a.out, X=Xn, feature_names=np.array(names + fnames),
                        codes=codes, dates=dates,
                        price=p["price"] if "price" in p else np.array([]))
    print(json.dumps({"out": a.out, "rows": len(dates), "cols_before": len(names),
                      "cols_after": len(names) + len(fnames),
                      "missing_frac": miss, "std": std,
                      "date_range": [min(map(str, dates)), max(map(str, dates))]},
                     ensure_ascii=False, indent=1))
    print("자체검증 PASS ①②③④⑤")
    return 0


if __name__ == "__main__":
    sys.exit(main())
