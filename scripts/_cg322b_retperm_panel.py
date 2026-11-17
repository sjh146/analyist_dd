"""CG322b — '수익률 순열' 널 패널 생성기 (CG322 1차 설계 폐기 후 교정판).

1차 설계(가격 수준을 날짜내 순열)는 **널이 아니라 초-예측 패널**이 됐다(실측 2026-11-18
03:2x: AUC 0.9069). 원인: 라벨이 p_other/p_self−1 이 되어 **자기 가격 수준**에 결정적으로
의존하고, 가격 수준은 피처(value_per·pbr·시총 계열)로 거의 복원된다. 즉 정보를 지운 게
아니라 '가격수준 → 라벨' 이라는 새 지름길을 만들었다.

교정 아이디어: **수준은 그대로 두고 날짜내 일별 수익률만 교란**한다.
  r_t = price_t / price_{t-1} − 1  (종목 내)
  → 같은 날짜 안에서 r 을 종목끼리 순열 → 종목별로 자기 시작가에서 재구성
  → 종목의 가격 수준·변동성 스케일은 보존되고, **라벨-피처 링크만 끊긴다**.
라벨이 '다른 종목의 수익률'이 되므로 널 기대 AUC = 0.5.

⚠ 자체점검에서 '라벨-피처 상관 파괴'를 직접 확인한다(종목상수 피처 quality_roa 와
   5일 선행수익의 상관 before/after). before 는 실측 0.55 AUC 수준의 상관이어야 하고
   after 는 |corr| < 0.02 여야 한다.
"""
from __future__ import annotations

import argparse
import json

import numpy as np

SRC = "/app/app/models/wf/panel_420_asofpatch.npz"
DST = "/app/app/models/wf/panel_420_asofpatch_retperm.npz"
OUT = "/app/reports/overnight/cg322b_retperm_panel.json"
SEED = 20261118


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--dst", default=DST)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--horizon", type=int, default=5)
    a = ap.parse_args()
    src, dst, out, seed, H = a.src, a.dst, a.out, a.seed, a.horizon

    d = np.load(src, allow_pickle=True)
    X = np.array(d["X"], copy=True)
    price = np.asarray(d["price"], dtype=float).copy()
    codes = np.array(d["codes"], copy=True)
    dates = np.array(d["dates"], copy=True)
    fn = np.array(d["feature_names"], copy=True)
    n = len(price)

    # ── 종목 내 날짜 정렬 순서 확보
    order = np.lexsort((dates, codes))
    r = np.full(n, np.nan)
    prev_idx = {}
    for i in order:
        c = codes[i]
        j = prev_idx.get(c)
        if j is not None:
            p0, p1 = price[j], price[i]
            if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                v = p1 / p0 - 1.0
                if -0.6 < v < 0.6:      # 상한가/데이터 오류는 교란 대상에서 제외
                    r[i] = v
        prev_idx[c] = i

    # ── 날짜내 r 순열
    rng = np.random.default_rng(seed)
    rp = r.copy()
    dsort = np.argsort(dates, kind="stable")
    _, starts = np.unique(dates[dsort], return_index=True)
    groups = np.split(dsort, starts[1:])
    moved = 0
    for g in groups:
        fin = g[np.isfinite(r[g])]
        if len(fin) < 2:
            continue
        perm = fin.copy()
        rng.shuffle(perm)
        rp[fin] = r[perm]
        moved += int((perm != fin).sum())

    # ── 재구성: 종목별 자기 시작가에서 (1+rp) 누적
    np_price = price.copy()
    for c in np.unique(codes):
        idx = order[codes[order] == c]
        base = None
        last = None
        for i in idx:
            p = price[i]
            if base is None:
                base = p if np.isfinite(p) and p > 0 else None
                last = base
                np_price[i] = base if base is not None else p
                continue
            if last is None or not np.isfinite(last):
                np_price[i] = p
                continue
            rr = rp[i]
            last = last * (1.0 + rr) if np.isfinite(rr) else last
            np_price[i] = last

    # ── 자체점검
    checks = {}
    ok1 = True
    for g in groups:
        a_ = r[g][np.isfinite(r[g])]
        b_ = rp[g][np.isfinite(r[g])]
        if not np.allclose(np.sort(a_), np.sort(b_)):
            ok1 = False
            break
    checks["1_return_multiset_per_date"] = bool(ok1)
    checks["2_X_codes_dates_identical"] = bool(
        np.array_equal(X, np.array(d["X"]), equal_nan=True)
        and np.array_equal(codes, d["codes"])
        and np.array_equal(dates, d["dates"])
    )
    checks["3_price_positive_finite"] = bool(
        np.isfinite(np_price).sum() >= np.isfinite(price).sum() * 0.99
        and np.nanmin(np_price[np.isfinite(np_price)]) > 0
    )
    checks["4_permutation_happened"] = bool(moved > 0.5 * n)
    checks["5_date_order_preserved"] = bool(np.array_equal(np.sort(dates), np.sort(d["dates"])))

    # 라벨-피처 링크 파괴 확인: 종목상수 대표 피처와 선행 H일 수익의 상관
    def label_corr(pr):
        fwd = np.full(n, np.nan)
        for c in np.unique(codes):
            idx = order[codes[order] == c]
            p = pr[idx]
            f = np.full(len(idx), np.nan)
            if len(idx) > H:
                f[:-H] = p[H:] / p[:-H] - 1.0
            fwd[idx] = f
        cols = [k for k, name in enumerate(fn) if name in
                ("quality_roa", "value_per", "quality_score")]
        if not cols:
            return None
        k = cols[0]
        m = np.isfinite(fwd) & np.isfinite(X[:, k])
        if m.sum() < 100:
            return None
        return {"feature": str(fn[k]), "corr": float(np.corrcoef(X[m, k], fwd[m])[0, 1]),
                "n": int(m.sum())}

    c_before, c_after = label_corr(price), label_corr(np_price)
    checks["6_label_link_broken"] = bool(
        c_after is not None and abs(c_after["corr"]) < 0.02
    )

    ok = all(checks.values())
    res = {
        "src": src, "dst": dst, "seed": seed, "horizon": H,
        "n_rows": int(n), "n_dates": int(len(groups)),
        "checks": checks, "all_pass": bool(ok),
        "permuted_return_cells": int(moved),
        "label_corr": {"before": c_before, "after": c_after},
    }
    if ok:
        np.savez_compressed(dst, X=X, price=np_price, codes=codes, dates=dates,
                            feature_names=fn)
        res["written"] = True
    else:
        res["written"] = False

    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
