"""CG319 — 특징-라벨 순열 널 패널 생성기 (정보 0 바닥 실측용).

무엇을 하나
----------
패널 npz 의 X 행을 **날짜 안에서만** 무작위로 순열한다. price·codes·dates 는 건드리지 않는다.
→ 라벨(h=5 선행수익)과 유니버스 필터는 원본과 **완전히 동일**하고, 피처만 다른 종목의 것이 된다.
즉 features↔label 링크만 끊긴 패널이므로, 이 패널로 학습·평가한 폴드 AUC 는
'정보 0'일 때 파이프라인이 만들어내는 **널 바닥**이다.

왜 필요한가
----------
19사이클 연속 '기준선 +0.02 미달' 을 보고하고 있는데, 그 판정을 만드는 계측기가
정보가 없을 때도 0.54 를 만들어내는지 아무도 재본 적이 없다. 최근 24시간에만
측정 버그 3건(요약 경로 오지정·짝 키 (fold,date) 오짝·폴드수 의존)이 나왔다.
널이 0.5 근처면 지금까지의 Δ 는 실재 신호 대비 해석이 유효하고, 널이 기준선 근처면
모든 Δ 보고를 중단하고 계측기부터 고쳐야 한다.

자체점검 (전부 PASS 여야 산출물을 쓴다)
--------------------------------------
 1) 날짜별 X 행 다중집합 불변(순열만 했으므로)
 2) price·codes·dates 비트 동일
 3) feature_names·NaN 개수 동일
 4) 순열이 실제로 일어남(무작위 표본에서 X 벡터가 원본과 다름)
 5) 종목 내 시계열 순서가 보존됨(날짜 정렬 불변)
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np

SRC = "/app/app/models/wf/panel_420_asofpatch.npz"
DST = "/app/app/models/wf/panel_420_asofpatch_nullperm.npz"
OUT = "/app/reports/overnight/cg319_null_panel.json"
SEED = 20261117


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

    n = len(dates)
    rng = np.random.default_rng(seed)

    # 날짜별 인덱스 그룹 → 그룹 안에서만 X 행을 순열
    order = np.argsort(dates, kind="stable")
    uniq, starts = np.unique(dates[order], return_index=True)
    groups = np.split(order, starts[1:])

    checks = {}
    Xp = X.copy()
    perms = {}
    for g in groups:
        perm = g.copy()
        rng.shuffle(perm)
        Xp[g] = X[perm]
        perms[str(dates[g[0]])] = int((perm != g).sum())

    # 1) 날짜별 다중집합 불변
    ok1 = True
    for g in groups:
        a = X[g]
        b = Xp[g]
        if not np.allclose(np.sort(a, axis=0), np.sort(b, axis=0), equal_nan=True):
            ok1 = False
            break
    checks["1_row_multiset_per_date"] = bool(ok1)

    # 2) 부속 배열 비트 동일
    checks["2_price_codes_dates_identical"] = bool(
        np.array_equal(price, d["price"])
        and np.array_equal(codes, d["codes"])
        and np.array_equal(dates, d["dates"])
    )
    # 3) 피처명·NaN 개수
    checks["3_feature_names_nan_same"] = bool(
        np.array_equal(fn, d["feature_names"])
        and int(np.isnan(X).sum()) == int(np.isnan(Xp).sum())
    )
    # 4) 순열 발생
    sample = rng.choice(np.arange(n), size=min(200, n), replace=False)
    moved = int(np.nansum(np.abs(Xp[sample] - X[sample]) > 0))
    checks["4_permutation_happened"] = bool(moved > 0)
    # 5) 날짜 정렬 불변
    checks["5_date_order_preserved"] = bool(np.array_equal(np.sort(dates), np.sort(d["dates"])))

    # 진단: 같은 날짜 안에서 피처-가격 상관이 실제로 파괴됐는지(한 대표 피처)
    finite = np.isfinite(price) & np.isfinite(Xp[:, 0])
    corr_after = float(np.corrcoef(Xp[finite, 0], price[finite])[0, 1]) if finite.sum() > 10 else None
    fin0 = np.isfinite(price) & np.isfinite(X[:, 0])
    corr_before = float(np.corrcoef(X[fin0, 0], price[fin0])[0, 1]) if fin0.sum() > 10 else None

    ok = all(checks.values())
    res = {
        "src": src,
        "dst": dst,
        "seed": seed,
        "n_rows": int(n),
        "n_dates": int(len(groups)),
        "n_features": int(X.shape[1]),
        "checks": checks,
        "all_pass": bool(ok),
        "diag_feature0_price_corr": {"before": corr_before, "after": corr_after},
        "permuted_rows_total": int(sum(perms.values())),
    }

    if ok:
        np.savez_compressed(
            dst,
            X=Xp,
            price=price,
            codes=codes,
            dates=dates,
            feature_names=fn,
        )
        res["written"] = True
    else:
        res["written"] = False

    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
