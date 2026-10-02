"""XR13 후속: date-blind(종목상수) 유사도·트윈 컬럼이 실제로 선별(edge)에 드는가 — 읽기 전용.

질문: similarity_std·avg_similarity_top10·twin_count·twin_avg_correlation·similar_count·
      authenticity_avg 는 종목당 유니크 1개(= date 인자를 받지 않는 getter 산물)다.
      이들이 top30 선별에 들어가 모델을 움직이는가? 아니면 CG67 처럼 기여 0 인가?

방법: 패널의 라벨(분위 0.30 · h5)에 대해 컬럼별 |AUC-0.5| 를 계산해 순위를 매기고,
      대상 컬럼들의 순위/값을 찍는다. (wf_label_sweep.subset 의 edge 와 같은 방향성 지표)

컨테이너: docker exec stock_xgboost_ml python /app/scripts/_xr13_selection_probe.py
"""
import os

import numpy as np

PANEL = os.environ.get("PANEL", "/app/app/models/wf/panel_420_asof3.npz")
TARGETS = [
    "similarity_std", "avg_similarity_top10", "max_similarity", "similar_count",
    "twin_count", "twin_avg_correlation", "authenticity_avg",
    "quality_roa", "quality_score", "value_per", "atr_pct", "bayes_momentum_5d",
]


def auc(y, x):
    m = np.isfinite(x)
    y, x = y[m], x[m]
    if y.min() == y.max() or x.min() == x.max():
        return 0.5, 0
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), float)
    ranks[order] = np.arange(1, len(x) + 1)
    # tie-average
    uniq, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    if (cnt > 1).any():
        sums = np.zeros(len(uniq))
        np.add.at(sums, inv, ranks)
        ranks = (sums / cnt)[inv]
    n1 = y.sum(); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return 0.5, int(len(y))
    a = (ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0)
    return a, int(len(y))


d = np.load(PANEL, allow_pickle=True)
names = [str(x) for x in d["feature_names"]]
X = d["X"].astype(float)
y = np.asarray(d["y"]).astype(float).ravel()
print(f"{os.path.basename(PANEL)}  X={X.shape}  rows={len(y)}  pos={int(y.sum())} ({100*y.mean():.1f}%)")

res = []
for j, n in enumerate(names):
    a, nn = auc(y, X[:, j])
    res.append((abs(a - 0.5), a, n, nn))
res.sort(reverse=True)
rank_of = {n: i + 1 for i, (_, _, n, _) in enumerate(res)}
print(f"\n상위 15 피처 (|AUC-0.5| 기준):")
for e, a, n, nn in res[:15]:
    tag = "  <== TARGET" if n in TARGETS else ""
    print(f"   {rank_of[n]:3d}. {n:34s} AUC={a:.4f}  (n={nn}){tag}")
print(f"\n대상 컬럼 등수:")
for n in TARGETS:
    if n in rank_of:
        for e, a, nn2, nn in res:
            if nn2 == n:
                print(f"   {n:34s} rank={rank_of[n]:3d}/{len(names)}  AUC={a:.4f}")
                break
    else:
        print(f"   {n:34s} ABSENT")
