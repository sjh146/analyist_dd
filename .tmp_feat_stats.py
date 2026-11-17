import numpy as np
d = np.load("/app/app/models/wf/panel_200liq_fresh.npz", allow_pickle=True)
X = d["X"].astype(np.float64)
names = [str(x) for x in d["feature_names"]]
pos = {n: i for i, n in enumerate(names)}
cands = ["news_count_5d", "news_count_20d", "sentiment_avg", "sentiment_avg_5d",
         "sns_attention_score", "sns_momentum_score", "sns_post_count",
         "disclosure_count_5d", "economic_event_count_7d",
         "foreign_net_buy_5d", "institution_net_buy_5d", "short_selling_ratio",
         "program_trading_ratio", "margin_balance_change", "credit_balance_change",
         "return_5d", "volatility_20d", "volume_ratio_5"]
for c in cands:
    i = pos.get(c)
    if i is None:
        print(f"{c:28s} MISSING")
        continue
    col = X[:, i]
    nz = np.count_nonzero(col)
    print(f"{c:28s} nonzero {nz/len(col):6.2%}  std {np.nanstd(col):10.4g}  min {np.nanmin(col):10.4g} max {np.nanmax(col):10.4g}")
print("rows", X.shape)
