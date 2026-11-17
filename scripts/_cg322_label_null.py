"""CG322 — 라벨(가격) 순열 널 패널 생성기. CG320 의 '남은 질문' 실측용.

무엇을 하나
----------
패널 npz 의 `price` 행을 **날짜 안에서만** 무작위로 순열한다. X·codes·dates 는 건드리지 않는다.
→ 피처는 원본 그대로이고 **라벨(선행 h일 수익)만 정보 0** 이 된다(가격 계열이 남의 종목 것이 됨).
CG319 는 반대(피처만 교란)였다 → 두 널을 같은 arm 으로 비교하면 편향의 출처가 갈린다.

  라벨널 AUC ≈ 0.5  → 편향은 **데이터/라벨 경로**(실제 가격의 시계열 자기상관·겹침)
  라벨널 AUC ≈ 0.51 → 편향은 **지표/학습 경로**(AUC 추정·조기종료·겹친 검증행)

자체점검 (전부 PASS 여야 산출물을 쓴다)
 1) 날짜별 price 다중집합 불변(순열만 했으므로)
 2) X·codes·dates 비트 동일(피처는 손대지 않았다)
 3) feature_names 동일·NaN 개수 동일
 4) 순열이 실제로 일어남(날짜 그룹의 95% 이상에서 행 이동)
 5) 날짜 순서 보존
"""
from __future__ import annotations

import argparse
import json

import numpy as np

SRC = "/app/app/models/wf/panel_420_asofpatch.npz"
DST = "/app/app/models/wf/panel_420_asofpatch_labperm.npz"
OUT = "/app/reports/overnight/cg322_label_null_panel.json"
SEED = 20261118


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--dst", default=DST)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    src, dst, out, seed = a.src, a.dst, a.out, a.seed

    d = np.load(src, allow_pickle=True)
    X = np.array(d["X"], copy=True)
    price = np.array(d["price"], copy=True)
    codes = np.array(d["codes"], copy=True)
    dates = np.array(d["dates"], copy=True)
    fn = np.array(d["feature_names"], copy=True)

    # price 스케일(수준/수정주가)에 따라 순열이 '0'이 될 수 있으므로 실측으로 확인한다
    rng = np.random.default_rng(seed)
    order = np.argsort(dates, kind="stable")
    _, starts = np.unique(dates[order], return_index=True)
    groups = np.split(order, starts[1:])

    pp = price.copy()
    permuted_groups = 0
    for g in groups:
        perm = g.copy()
        rng.shuffle(perm)
        pp[g] = price[perm]
        if int((perm != g).sum()) > 0:
            permuted_groups += 1

    checks = {}
    ok1 = True
    for g in groups:
        a_, b_ = price[g], pp[g]
        if not np.allclose(np.sort(a_, axis=0), np.sort(b_, axis=0), equal_nan=True):
            ok1 = False
            break
    checks["1_price_multiset_per_date"] = bool(ok1)
    # ⚠ array_equal 은 NaN 을 같다고 보지 않는다 → 피처 X 는 equal_nan 으로 비교
    checks["2_X_codes_dates_identical"] = bool(
        np.array_equal(X, np.array(d["X"]), equal_nan=True)
        and np.array_equal(codes, d["codes"])
        and np.array_equal(dates, d["dates"])
    )
    checks["3_feature_names_nan_same"] = bool(
        np.array_equal(fn, d["feature_names"])
        and int(np.isnan(X).sum()) == int(np.isnan(np.array(d["X"])).sum())
        and int(np.isnan(price).sum()) == int(np.isnan(pp).sum())
    )
    checks["4_permutation_happened"] = bool(permuted_groups >= 0.95 * len(groups))
    checks["5_date_order_preserved"] = bool(
        np.array_equal(np.sort(dates), np.sort(d["dates"]))
    )
    ok = all(checks.values())

    res = {
        "src": src, "dst": dst, "seed": seed,
        "n_rows": int(len(dates)), "n_dates": int(len(groups)),
        "checks": checks, "all_pass": bool(ok),
        "permuted_groups": int(permuted_groups), "n_groups": int(len(groups)),
    }
    if ok:
        np.savez_compressed(dst, X=X, price=pp, codes=codes, dates=dates,
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
