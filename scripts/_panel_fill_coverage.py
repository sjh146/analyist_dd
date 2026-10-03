#!/usr/bin/env python3
"""패널 열별 '채움/비영' 커버리지 프로브 — CG79(빌드 시점 결측 플래그)의 전제 검정.

질문: nan->0 대체로 사라진 결측을 per-cell 플래그로 되살릴 가치가 있는가?
  -> 열 단위 커버리지가 이분포(0% / 88~98%)면 per-cell 지시자는 열 지시자와 동일 = 무가치.
  -> 중간 구간(5~80%) 열이 여럿이면 per-cell 결측 정보가 실재 = 빌드 플래그 가치 있음.

읽기 전용. 컨테이너에서 실행:
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_panel_fill_coverage.py \
      /app/app/models/wf/panel_prod200.npz [more.npz ...]
"""
import sys
import numpy as np


def probe(path):
    d = np.load(path, allow_pickle=True)
    X = d["X"]
    names = [str(n) for n in d["feature_names"]]
    codes = d["codes"]
    n, k = X.shape
    print("=" * 78)
    print(f"{path}  X={X.shape}  codes={len(set(codes.tolist()))}")
    nan_frac = np.isnan(X).mean(axis=0) if np.isnan(X).any() else np.zeros(k)
    nz = (X != 0) & ~np.isnan(X)
    nz_frac = nz.mean(axis=0)
    # 종목별 유니크값 (중앙값) — 종목 상수 판별
    uniq = []
    for j in range(k):
        vals = X[:, j]
        per = {}
        for c, v in zip(codes, vals):
            per.setdefault(c, set()).add(float(v))
        uniq.append(int(np.median([len(s) for s in per.values()])))
    uniq = np.array(uniq)
    buckets = [(0.0, 0.0), (0.0, 0.05), (0.05, 0.20), (0.20, 0.50), (0.50, 0.80),
               (0.80, 0.95), (0.95, 0.999), (0.999, 1.01)]
    print(f"  전체 NaN 비율 = {nan_frac.mean():.4f} (NaN 있는 열 {int((nan_frac>0).sum())}/{k})")
    print("  비영 커버리지 분포:")
    for lo, hi in buckets:
        m = (nz_frac > lo) & (nz_frac <= hi)
        print(f"    ({lo:.3f}, {hi:.3f}] : {int(m.sum()):>3} 열")
    mid = (nz_frac > 0.05) & (nz_frac < 0.80)
    dead = nz_frac <= 0.001
    print(f"  중간구간(5~80%) = {int(mid.sum())} 열   전행0 = {int(dead.sum())} 열")
    # 종목상수(중앙 유니크<=1) 와 중간구간 교차
    midc = mid & (uniq > 1)
    print(f"  중간구간 & 시간가변(종목당 유니크>1) = {int(midc.sum())} 열")
    if midc.sum() > 0:
        order = np.argsort(-nz_frac[midc])
        idx = np.where(midc)[0][order]
        print("  중간구간 시간가변 상위 15:")
        for j in idx[:15]:
            print(f"    {names[j]:<34} cov={nz_frac[j]:.3f} uniq(med)={uniq[j]}")
    print("  전행0 목록:")
    print("   ", ", ".join(names[j] for j in np.where(dead)[0]))
    return dict(mid=int(mid.sum()), mid_moving=int(midc.sum()), dead=int(dead.sum()))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        probe(p)
