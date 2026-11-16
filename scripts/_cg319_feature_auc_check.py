"""CG319 부검 — 순열 널 패널에서 '아직 살아 있는' 피처가 있는가.

왜 필요한가
----------
순열 널 폴드평균이 0.5158 로 0.5 보다 높게 나왔다. 해석은 둘 중 하나다.
 (a) 계측기 편향(선별·지표·겹침창) → 널이 0.5 를 넘는다.
 (b) 순열이 일부 피처를 못 끊었다(런타임에 price/codes 로 재계산되는 파생 피처) → 널 패널에
     실제 정보가 남아 있다.
이 스크립트는 (b) 를 직접 본다. 원본 패널과 순열 패널에서 **단일 피처 날짜별 AUC** 를 각각 계산해
  · 널에서 |AUC−0.5| 가 큰 피처가 남아 있는지
  · 그 피처가 원본에서도 같은 방향인지
를 출력한다. 널 단일피처 |AUC−0.5| 최대값이 작으면(예: <0.02) (a) 쪽이고, 크면 (b) 다.
"""
from __future__ import annotations

import json
import sys

import numpy as np

CTL = "/app/app/models/wf/panel_420_asofpatch.npz"
NULL = "/app/app/models/wf/panel_420_asofpatch_nullperm.npz"
OUT = "/app/reports/overnight/cg319_feature_auc_check.json"


def date_auc(y: np.ndarray, s: np.ndarray) -> float | None:
    """라벨 y(0/1) 와 점수 s 의 AUC(랭크 기반, 동점 평균)."""
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return None
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    ranks[order] = np.arange(1, len(s) + 1, dtype=float)
    # 동점 평균 랭크
    uniq, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    if len(uniq) < len(s):
        sums = np.zeros(len(uniq))
        np.add.at(sums, inv, ranks)
        ranks = (sums / cnt)[inv]
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def per_feature_auc(path: str) -> dict:
    d = np.load(path, allow_pickle=True)
    X = d["X"].astype(np.float64)
    dates = d["dates"]
    price = d["price"].astype(np.float64)
    codes = d["codes"]
    fn = [str(x) for x in d["feature_names"]]

    # 라벨: 종목별 h=5 선행수익 → 날짜내 분위 0.30 (스윕과 같은 정의)
    import pandas as pd  # noqa: PLC0415

    df = pd.DataFrame({"code": codes, "date": dates, "price": price})
    df = df.sort_values(["code", "date"], kind="mergesort")
    fwd = df.groupby("code", sort=False)["price"].transform(lambda s: s.shift(-5) / s - 1.0)
    df["fwd"] = fwd.values
    df = df.dropna(subset=["fwd"])
    q = df.groupby("date")["fwd"].transform(lambda s: s.quantile(0.70))
    df["y"] = (df["fwd"] >= q).astype(int)

    # 원본 행 순서로 되돌리기
    idx = np.argsort(df.index.values, kind="mergesort")
    df = df.iloc[idx]
    y = df["y"].values
    dates_sorted = df["date"].values
    Xs = X[df.index.values]

    res = {}
    groups = {}
    for i, dt in enumerate(dates_sorted):
        groups.setdefault(dt, []).append(i)
    for j, name in enumerate(fn):
        col = Xs[:, j]
        aucs = []
        for dt, g in groups.items():
            g = np.array(g)
            v = col[g]
            ok = np.isfinite(v)
            if ok.sum() < 8:
                continue
            a = date_auc(y[g][ok].astype(np.int64), v[ok])
            if a is not None:
                aucs.append(a)
        if aucs:
            res[name] = {
                "auc_mean": float(np.mean(aucs)),
                "n_dates": len(aucs),
                "auc_std": float(np.std(aucs)),
            }
    return res


def main() -> int:
    ctl = per_feature_auc(CTL)
    nul = per_feature_auc(NULL)

    common = sorted(set(ctl) & set(nul))
    rows = [
        {
            "feature": f,
            "ctl": ctl[f]["auc_mean"],
            "null": nul[f]["auc_mean"],
            "ctl_edge": abs(ctl[f]["auc_mean"] - 0.5),
            "null_edge": abs(nul[f]["auc_mean"] - 0.5),
        }
        for f in common
    ]
    rows.sort(key=lambda r: -r["null_edge"])
    edges = np.array([r["null_edge"] for r in rows])

    res = {
        "n_features": len(rows),
        "null_edge_mean": float(edges.mean()),
        "null_edge_p95": float(np.percentile(edges, 95)),
        "null_edge_max": float(edges.max()),
        "null_features_edge_ge_0.044": int((edges >= 0.044).sum()),
        "top20_by_null_edge": rows[:20],
        "ctl_edge_top10": sorted(rows, key=lambda r: -r["ctl_edge"])[:10],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "top20_by_null_edge"}, ensure_ascii=False, indent=1))
    print("top5 null:", json.dumps(res["top20_by_null_edge"][:5], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
