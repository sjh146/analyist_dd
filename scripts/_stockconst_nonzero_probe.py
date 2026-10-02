"""종목상수 컬럼의 '비영 상수' 비율 — 스냅샷(누수) 후보 vs 구조적 0(결측) 구분 (읽기 전용).

앞선 프로브(_stockconst_probe.py)는 '종목별 유니크 중앙 1' 컬럼을 74개로 셌는데,
그 안에는 ① 이벤트처럼 대부분 0 이라 상수인 컬럼(결측, 누수 아님)과
② 종목 상수값이 0 이 아닌 컬럼(=어떤 시점 값이 모든 과거 행에 복사된 스냅샷, 누수 후보)이 섞여 있다.
여기서 ②만 골라낸다.

출력: 상수값 != 0 인 종목 비율 (share_nonzero_const) 과 그 상수들의 통계.
컨테이너: docker exec stock_xgboost_ml python /app/scripts/_stockconst_nonzero_probe.py
"""
import json
import os

import numpy as np

PANEL = os.environ.get("PANEL", "/app/app/models/wf/panel_420_asof3.npz")
OUT = os.environ.get("OUT", "/app/reports/overnight/stockconst_nonzero.json")

d = np.load(PANEL, allow_pickle=True)
names = [str(x) for x in d["feature_names"]]
X = d["X"].astype(float)
codes = np.asarray(d["codes"])
ucodes = np.unique(codes)

rows = []
for j, n in enumerate(names):
    col = X[:, j]
    const_vals = []
    for c in ucodes:
        v = np.unique(col[codes == c])
        if len(v) == 1:
            const_vals.append(v[0])
    if not const_vals:
        continue
    cv = np.asarray(const_vals, float)
    share_const = len(cv) / len(ucodes)               # 종목 중 상수인 비율
    share_nonzero = float(np.mean(cv != 0.0))          # 그 상수값이 0 이 아닌 비율
    rows.append((n, share_const, share_nonzero, float(np.mean(cv))))

snap = [r for r in rows if r[1] >= 0.5 and r[2] >= 0.5]
print(f"{os.path.basename(PANEL)}  cols={len(names)}  stocks={len(ucodes)}")
print(f"종목 50%+ 상수 & 상수값 비영 50%+ 인 컬럼 = {len(snap)}개 (스냅샷/누수 후보)")
for n, sc, snz, mv in sorted(snap, key=lambda r: -r[2]):
    print(f"   {n:34s} 종목상수비율={sc:5.2f} 비영상수비율={snz:5.2f} 상수평균={mv:.4g}")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump({"panel": PANEL, "n_cols": len(names), "snapshot_candidates":
               [{"name": n, "share_stock_constant": round(sc, 3), "share_nonzero_constant": round(snz, 3),
                 "const_mean": mv} for n, sc, snz, mv in snap]}, f, indent=1)
print("\nwrote", OUT)
