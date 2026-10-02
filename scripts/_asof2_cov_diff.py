"""CG65 후속: as-of 수리(panel_420_asofpatch → panel_420_asof2)가 어떤 피처의
커버리지(비영 비율)를 무너뜨렸는지 전수 대조. 읽기 전용.

수리 전/후 같은 13,609행·210피처에서 비영률(nz) 을 피처별로 비교해
하락폭 큰 순으로 출력한다.
"""
import sys

import numpy as np

PRE = sys.argv[1] if len(sys.argv) > 1 else "/app/app/models/wf/panel_420_asofpatch.npz"
POST = sys.argv[2] if len(sys.argv) > 2 else "/app/app/models/wf/panel_420_asof2.npz"


def load(p):
    z = np.load(p, allow_pickle=True)
    return z["X"].astype(float), [str(f) for f in z["feature_names"]]


Xp, fp = load(PRE)
Xq, fq = load(POST)
assert fp == fq, "feature order differs"
nzp = (Xp != 0).mean(axis=0)
nzq = (Xq != 0).mean(axis=0)
d = nzq - nzp
order = np.argsort(d)
print("=== 비영률 하락 상위 25 (수리로 죽은 피처) ===")
for i in order[:25]:
    print(f"  {fp[i]:34s} pre={nzp[i]:6.3f} post={nzq[i]:6.3f} Δ={d[i]:+6.3f}")
print("\n=== 비영률 상승 상위 10 ===")
for i in order[::-1][:10]:
    print(f"  {fp[i]:34s} pre={nzp[i]:6.3f} post={nzq[i]:6.3f} Δ={d[i]:+6.3f}")
print(f"\n하락(Δ<-0.30) {int((d < -0.30).sum())}개 / 상승(Δ>+0.30) {int((d > 0.30).sum())}개 / 총 {len(fp)}개")
print(f"post 비영률 0.5 미만 피처: {int((nzq < 0.5).sum())}개")
print(f"pre  비영률 0.5 미만 피처: {int((nzp < 0.5).sum())}개")
