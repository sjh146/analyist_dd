"""종목상수(시간 불변) 컬럼 목록 — date 인자를 받지 않는 getter 의 산물 식별 (읽기 전용).

판정: (stock,date) 패널에서 종목별 유니크값의 **중앙값이 1** 이면 그 컬럼은 그 종목에서
시간에 따라 변하지 않는다(= 빌드 시점 스냅샷을 모든 과거 행에 복사한 흔적, as-of 위반 후보).

컨테이너: docker exec stock_xgboost_ml python /app/scripts/_stockconst_probe.py
"""
import os

import numpy as np

PANEL = os.environ.get("PANEL", "/app/app/models/wf/panel_420_asof3.npz")
FAMILY = ("similar", "twin", "sector", "theme", "cycle", "authenticity")

d = np.load(PANEL, allow_pickle=True)
names = [str(x) for x in d["feature_names"]]
X = d["X"].astype(float)
codes = np.asarray(d["codes"])
ucodes = np.unique(codes)

const, varying = [], []
for j, n in enumerate(names):
    col = X[:, j]
    uq = [len(np.unique(col[codes == c])) for c in ucodes]
    med = float(np.median(uq))
    (const if med <= 1.0 else varying).append((n, med, max(uq)))

print(f"{os.path.basename(PANEL)}  rows={len(codes)} stocks={len(ucodes)} cols={len(names)}")
print(f"종목상수(중앙 유니크<=1): {len(const)} / 시간가변: {len(varying)}")
print("\n-- 종목상수 컬럼 전체 --")
for n, med, mx in const:
    tag = " <== sim/twin/theme 계열" if any(k in n for k in FAMILY) else ""
    print(f"   {n:34s} med_uniq={med:4.0f} max_uniq={mx:5d}{tag}")
print("\n-- sim/twin/theme 계열 중 시간가변인 것 --")
for n, med, mx in varying:
    if any(k in n for k in FAMILY):
        print(f"   {n:34s} med_uniq={med:5.0f} max_uniq={mx:5d}")
