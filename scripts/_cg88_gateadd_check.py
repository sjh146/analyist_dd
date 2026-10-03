#!/usr/bin/env python3
"""CG88 후보 config(g11/pv10)의 gate_add 컬럼이 panel_prod200 에 존재하는지 확인."""
import os

import numpy as np

P = "/app/app/models/wf/panel_prod200.npz"
if not os.path.exists(P):
    P = "/home/jhshi/analyist_dd/services/xgboost-ml/app/models/wf/panel_prod200.npz"
z = np.load(P, allow_pickle=True)
names = set(str(x) for x in z["feature_names"])
G11 = ["atr_pct", "authenticity_avg", "bayes_gain_uncertainty", "bayes_momentum_1d",
       "bayes_momentum_5d", "bayes_volatility", "quality_beta",
       "quality_price_volatility_60d", "similarity_std", "twin_avg_correlation", "twin_count"]
PV10 = ["sentiment_avg_db", "sentiment_momentum", "ma_position_20", "institution_net_buy_5d",
        "rank_volume_ratio_20", "bb_position", "volume_ratio_5", "rsi",
        "similar_stocks_return_std", "rank_volume_ratio_5"]
for label, group in (("g11", G11), ("pv10", PV10)):
    miss = [n for n in group if n not in names]
    print(f"{label}: {len(group) - len(miss)}/{len(group)} 존재 · 누락={miss}")

X = z["X"].astype(float)
fill = {n: float(np.mean(~np.isnan(X[:, i]))) for i, n in enumerate([str(x) for x in z["feature_names"]])}
print("\n[gate_add 후보 커버리지(비결측 비율)]")
for n in G11 + PV10:
    if n in fill:
        print(f"  {n:34s} fill={fill[n]:.4f}")
