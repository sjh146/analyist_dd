"""CG21 착수 전 정찰: panel_150u 에서 date 별 '공통 후보집합' 크기를 실측한다.
restrict-q 0.05 의 pool 은 두 arm 행의 합집합 = q0.30 arm 의 행(그날 상하위 30%)이다.
pool ~= 0.6n, sel(=pool 의 양쪽 5% 꼬리) ~= 0.1*0.6n, q0.05 arm 후보 = |arm05 ∩ sel|.
k=3/5/10 이 실제로 측정 가능한지(--min-pool 20 통과 포함)를 학습 없이 센다.
"""
import numpy as np, pandas as pd
z = np.load("/app/app/models/wf/panel_150u.npz", allow_pickle=True)
df = pd.DataFrame({"date": [str(d) for d in z["dates"]],
                   "code": [str(c) for c in z["codes"]],
                   "price": z["price"].astype(float)})
df["ret"] = df.groupby("code", sort=False)["price"].transform(lambda s: s.shift(-5)/s - 1.0)
rows = []
for d, g in df.groupby("date"):
    g = g.dropna(subset=["ret"])
    n = len(g)
    if n < 20:
        continue
    r = g["ret"].values
    hi30 = np.quantile(r, 0.70); lo30 = np.quantile(r, 0.30)
    hi05 = np.quantile(r, 0.95); lo05 = np.quantile(r, 0.05)
    arm30 = r[(r > hi30) + (r < lo30) > 0]
    arm05 = r[(r > hi05) + (r < lo05) > 0]
    pool = arm30
    sel_hi = np.quantile(pool, 0.95); sel_lo = np.quantile(pool, 0.05)
    sel = pool[(pool >= sel_hi) + (pool <= sel_lo) > 0]
    cand05 = len(set(np.round(arm05, 12)).intersection(set(np.round(sel, 12))))
    rows.append((d, n, len(arm30), len(arm05), len(sel), cand05))
t = pd.DataFrame(rows, columns=["date", "n", "pool_q30", "rov_q05", "sel", "cand_q05"])
print(f"dates={len(t)}  stocks/day median={int(t.n.median())} (min {t.n.min()} max {t.n.max()})")
print(f"pool median={t.pool_q30.median():.0f} (min {int(t.pool_q30.min())})  min_pool=20 전부통과={bool((t.pool_q30 >= 20).all())}")
print(f"sel(공통 후보) median={t.sel.median():.1f}")
print(f"q0.05 arm 후보 median={t.cand_q05.median():.1f} (min {int(t.cand_q05.min())})")
for k in (3, 5, 10):
    ok = int((t.cand_q05 >= k).sum())
    print(f"  k={k}: 측정가능 날짜 {ok}/{len(t)} ({100*ok/len(t):.0f}%)")
