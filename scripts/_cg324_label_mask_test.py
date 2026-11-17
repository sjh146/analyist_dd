#!/usr/bin/env python3
"""CG324 자체점검 — '분위 기준 모집단 마스크'가 실제로 무엇을 바꾸는가 (컨테이너 내부 실행).

검사(전부 PASS 여야 pending 승격):
  1) label_mask=None 경로가 기존과 **비트 동일**(회귀 없음).
  2) 마스크를 줘도 **행 수·라벨 커버리지**가 유지된다(행을 버리지 않는다).
  3) 마스크가 실제로 분위 문턱을 바꾼다 — 날짜의 과반에서 hi/lo 가 달라진다.
  4) 결정적(같은 입력 두 번 → 동일 결과) · 마스크 비율이 0<keep_frac<1.
  5) 라벨 뒤집힘률이 0 이 아니다(= arm 이 no-op 이 아니다).

실행: docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg324_label_mask_test.py'
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_wave as W                      # noqa: E402
import wf_label_sweep as S               # noqa: E402

PANEL = sys.argv[1] if len(sys.argv) > 1 else "/app/app/models/wf/panel_420_tv.npz"
RESULTS: list = []


def check(name, ok, detail):
    RESULTS.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}", flush=True)


def main():
    z = np.load(PANEL, allow_pickle=True)
    X = z["X"]
    names = [str(c) for c in z["feature_names"]]
    df = pd.DataFrame(X, columns=names)
    df["price"] = np.asarray(z["price"], dtype=float)
    df["stock_code"] = np.asarray(z["codes"]).astype(str)
    df["date"] = np.asarray(z["dates"]).astype(str)
    print(f"패널 {PANEL}: {len(df)}행 · {df['stock_code'].nunique()}종목 · "
          f"{df['date'].nunique()}날짜 · 피처 {len(names)}", flush=True)

    spec = {"daychg_lt": 25.0, "col": "tv_log", "col_gte": 20.72}
    mask, diag = S.build_label_mask(df, spec)
    print("마스크 진단:", diag, flush=True)

    # 1) None 경로 비트 동일
    y0 = W.make_labels(df, "quantile", 5, 0.30)
    y0b = W.make_labels(df, "quantile", 5, 0.30, label_mask=None)
    same = np.array_equal(np.isnan(y0), np.isnan(y0b)) and np.array_equal(
        np.nan_to_num(y0, nan=-9.0), np.nan_to_num(y0b, nan=-9.0))
    check("1) label_mask=None 회귀 없음(비트 동일)", same,
          f"라벨 {int(np.isfinite(y0).sum())}행 · 동일={same}")

    # 2) 행 유지 · 커버리지
    y1 = W.make_labels(df, "quantile", 5, 0.30, label_mask=mask)
    cov0 = float(np.mean(np.isfinite(y0)))
    cov1 = float(np.mean(np.isfinite(y1)))
    check("2) 행 유지·라벨 커버리지 보존", (len(y1) == len(y0)) and cov1 >= 0.90 * cov0,
          f"행 {len(y0)}→{len(y1)} · 커버리지 {cov0:.4f}→{cov1:.4f} "
          f"(비율 {cov1/max(cov0,1e-9):.3f})")

    # 3) 문턱이 실제로 바뀌는가 — 날짜별 hi/lo 비교
    price = df.groupby("stock_code", sort=False)["price"]
    ret = price.transform(lambda s: s.shift(-5) / s - 1.0)
    day = df["date"]
    hi0 = ret.groupby(day).transform(lambda s: s.quantile(0.70))
    lo0 = ret.groupby(day).transform(lambda s: s.quantile(0.30))
    ref = ret.where(pd.Series(mask, index=df.index))
    hi1 = ref.groupby(day).transform(lambda s: s.quantile(0.70))
    lo1 = ref.groupby(day).transform(lambda s: s.quantile(0.30))
    d = pd.DataFrame({"date": day.values, "hi0": hi0.values, "hi1": hi1.values,
                      "lo0": lo0.values, "lo1": lo1.values}).dropna()
    per_day = d.groupby("date").first()
    changed = (np.abs(per_day["hi1"] - per_day["hi0"]) > 1e-12) | \
              (np.abs(per_day["lo1"] - per_day["lo0"]) > 1e-12)
    frac = float(changed.mean())
    check("3) 마스크가 분위 문턱을 바꾼다(날짜 과반)", frac >= 0.50,
          f"문턱이 달라진 날짜 {int(changed.sum())}/{len(changed)} = {frac:.1%} · "
          f"hi 평균 이동 {float((per_day['hi1']-per_day['hi0']).mean()):+.5f} · "
          f"lo 평균 이동 {float((per_day['lo1']-per_day['lo0']).mean()):+.5f}")

    # 4) 결정성 · 마스크 비율
    mask2, _ = S.build_label_mask(df, spec)
    y1b = W.make_labels(df, "quantile", 5, 0.30, label_mask=mask2)
    det = np.array_equal(np.nan_to_num(y1, nan=-9.0), np.nan_to_num(y1b, nan=-9.0))
    check("4) 결정적 · 마스크 비율 0~1", det and 0.0 < float(mask.mean()) < 1.0,
          f"2회 동일={det} · keep_frac={float(mask.mean()):.3f}")

    # 5) arm 이 no-op 이 아니다
    both = np.isfinite(y0) & np.isfinite(y1)
    flip = float(np.mean(y0[both] != y1[both]))
    check("5) 라벨 뒤집힘 > 0 (no-op 아님)", flip > 0.0,
          f"양쪽 라벨 존재 {int(both.sum())}행 중 뒤집힘 {flip:.1%}")

    n_pass = sum(1 for _, ok in RESULTS if ok)
    print(f"\n결과: {n_pass}/{len(RESULTS)} PASS", flush=True)
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
