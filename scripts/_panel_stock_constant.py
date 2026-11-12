"""패널 종목상수 피처 탐지 (읽기 전용) — CG204 exclude_names 교정 근거."""
import json
import sys

import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "/app/app/models/wf/panel_200liq_fresh.npz"
z = np.load(path, allow_pickle=True)
X = z["X"]
names = [str(s) for s in z["feature_names"]]
codes = np.asarray(z["codes"], dtype=object)
dates = np.asarray(z["dates"], dtype=object)
print("rows", X.shape[0], "cols", X.shape[1], "codes", len(set(codes.tolist())),
      "dates", len(set(dates.tolist())), "date_range", str(min(dates)), "~", str(max(dates)))

out = []
for j, name in enumerate(names):
    col = X[:, j]
    const_codes = 0
    n_codes = 0
    for c in np.unique(codes):
        m = codes == c
        v = col[m]
        v = v[~np.isnan(v)]
        n_codes += 1
        if v.size and np.unique(v).size == 1:
            const_codes += 1
    out.append((name, const_codes / max(n_codes, 1), float(np.nanstd(col))))

st = [o for o in out if o[1] >= 0.99]
print("stock_constant(>=0.99):", len(st))
print("stock_constant(>=0.80):", len([o for o in out if o[1] >= 0.80]))
for o in sorted(st, key=lambda x: -x[1]):
    print(f"  {o[0]:40s} const_ratio={o[1]:.3f} std={o[2]:.6g}")
print("zero_std_cols:", sum(1 for o in out if o[2] == 0.0))

cg204 = ["theme_count", "theme_max_relevance", "theme_exposure_5d", "similarity_std", "similar_count",
         "twin_avg_correlation", "twin_count", "avg_similarity_top10", "max_similarity", "sector_count",
         "authenticity_avg", "negative_ratio", "positive_ratio", "sentiment_avg", "sentiment_avg_5d",
         "sentiment_avg_20d", "sentiment_avg_db", "sentiment_momentum", "sentiment_trend",
         "sentiment_volatility", "news_count_5d", "news_count_20d", "short_interest_ratio",
         "days_to_cover", "etf_flow_5d", "institution_ownership_pct", "margin_balance_change",
         "credit_balance_change", "value_ncav"]
present = [n for n in cg204 if n in names]
print("CG204 exclude_names 중 패널에 존재:", present)
