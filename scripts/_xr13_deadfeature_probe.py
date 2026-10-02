"""XR13 진단용 프로브 — '부활가능 11개' 피처가 패널에서 왜 0 인가 (읽기 전용).

리서처 R13 분해: 원천 데이터가 이미 있는데 파이프라인 값이 전부 0 인 피처 11종.
  atr_pct · quality_beta · quality_price_volatility_60d · authenticity_avg ·
  similarity_std · twin_count · twin_avg_correlation ·
  bayes_momentum_1d · bayes_momentum_5d · bayes_volatility · bayes_gain_uncertainty

판정 기준: 패널 컬럼 비영 비율(nonzero%) + 종목별 유니크값(종목상수 지문).
  nonzero% == 0 이면 '패널에 값이 아예 안 들어갔다'(배선/빌더 문제)
  nonzero% > 0 인데 uniq <= 1 이면 '종목상수'(정보 없음)

컨테이너에서 실행:  docker exec stock_xgboost_ml python /app/scripts/_xr13_deadfeature_probe.py
"""
import os
import sys

import numpy as np

PANELS = [
    "/app/app/models/wf/panel_420_asof3.npz",
    "/app/app/models/wf/panel_420_asofpatch.npz",
    "/app/app/models/wf/panel_150u.npz",
    "/app/app/models/wf/panel_995.npz",
]

TARGETS = [
    "atr_pct",
    "quality_beta",
    "quality_price_volatility_60d",
    "authenticity_avg",
    "similarity_std",
    "twin_count",
    "twin_avg_correlation",
    "bayes_momentum_1d",
    "bayes_momentum_5d",
    "bayes_volatility",
    "bayes_gain_uncertainty",
    # 참고(같은 계열, L3 에서 '이미 존재'로 기록된 것들)
    "volatility_20d",
    "volatility_60d",
    "beta_60d",
    "avg_similarity_top10",
    "similar_count",
]


def probe(path):
    if not os.path.exists(path):
        print(f"MISSING {path}")
        return None
    d = np.load(path, allow_pickle=True)
    names = [str(x) for x in d["feature_names"]] if "feature_names" in d else []
    X = d["X"].astype(float)
    print("=" * 92)
    print(f"{os.path.basename(path)}  X={X.shape}  nfeat={len(names)}")
    if len(names) != X.shape[1]:
        print("  !! feature_names length != X columns")
    nz_all = np.array([np.count_nonzero(X[:, j]) for j in range(X.shape[1])], float)
    dead = int((nz_all == 0).sum())
    print(f"  전체 컬럼 중 전 행 0(완전사망): {dead}/{X.shape[1]}")
    for n in TARGETS:
        if n not in names:
            print(f"   {n:32s} ABSENT")
            continue
        j = names.index(n)
        col = X[:, j]
        nz = 100.0 * np.count_nonzero(col) / len(col)
        uniq = len(np.unique(col))
        stock_uniq = None
        if "stock_codes" in d:
            codes = np.asarray(d["stock_codes"])
            try:
                stock_uniq = int(np.median([len(np.unique(col[codes == c])) for c in np.unique(codes)]))
            except Exception:
                stock_uniq = None
        print(
            f"   {n:32s} nonzero={nz:6.2f}%  uniq={uniq:5d}  종목당유니크중앙={stock_uniq}  "
            f"min={col.min():.4g} max={col.max():.4g} std={col.std():.4g}"
        )
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for p in sys.argv[1:]:
            probe(p)
    else:
        for p in PANELS:
            probe(p)
