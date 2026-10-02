"""결측(NaN) 비율 전수 + 스냅샷 제외목록 확정 (읽기 전용).

① 패널 컬럼별 NaN 비율 — NaN 지배 컬럼은 모델에 '결측'으로 들어가 무정보가 된다(조용한 사망).
② 스냅샷(종목 50%+ 상수 & 상수값 비영 50%+) 컬럼 목록을, NaN 지배 컬럼을 뺀 형태로 확정해
   exclude_names 로 바로 쓸 수 있게 출력한다.

컨테이너: docker exec stock_xgboost_ml python /app/scripts/_nan_exclude_probe.py
"""
import json
import os

import numpy as np

PANEL = os.environ.get("PANEL", "/app/app/models/wf/panel_420_asof3.npz")

d = np.load(PANEL, allow_pickle=True)
names = [str(x) for x in d["feature_names"]]
X = d["X"].astype(float)
codes = np.asarray(d["codes"])
ucodes = np.unique(codes)

nan_ratio = np.array([np.mean(~np.isfinite(X[:, j])) for j in range(X.shape[1])])
dead_nan = [(names[j], float(nan_ratio[j])) for j in np.argsort(-nan_ratio) if nan_ratio[j] > 0.5]
print(f"{os.path.basename(PANEL)}  X={X.shape}")
print(f"NaN 비율 > 50% 컬럼: {len(dead_nan)}개")
for n, r in dead_nan:
    print(f"   {n:34s} nan={r*100:6.2f}%")

snap, zero_const = [], []
for j, n in enumerate(names):
    if nan_ratio[j] > 0.5:
        continue
    col = X[:, j]
    cv = [v[0] for c in ucodes for v in [np.unique(col[codes == c])] if len(v) == 1]
    if len(cv) / len(ucodes) < 0.5:
        continue
    cv = np.asarray(cv, float)
    if float(np.mean(cv != 0.0)) >= 0.5:
        snap.append(n)
    else:
        zero_const.append(n)

print(f"\n스냅샷 후보(종목 50%+ 상수 · 상수값 비영 50%+, NaN 지배 제외): {len(snap)}개")
print(json.dumps(snap, ensure_ascii=False))
print(f"\n구조적 0 상수(결측) 컬럼 {len(zero_const)}개(참고): {json.dumps(zero_const, ensure_ascii=False)[:400]}")
