import pandas as pd

t = pd.read_csv("data/reports/close_gate_probe/trades.csv")
print("행", len(t), "· 세션", t["date"].nunique())
gate_cols = ["gate_ok", "r1_ok", "heat_ok"]
for c in gate_cols:
    v = t[c]
    passed = int(v.fillna(False).astype(bool).sum())
    print("  {0}: dtype={1} 고유값={2} 통과수={3}".format(
        c, v.dtype, sorted(map(str, v.dropna().unique()))[:4], passed))
mask = t[gate_cols].fillna(False).astype(bool).all(axis=1)
print("게이트 전부 통과 행:", int(mask.sum()), "· 세션:", t[mask]["date"].nunique() if mask.sum() else 0)
print("다음시가 수익률 있는 행:", int(t["ret_next_open_pct"].notna().sum()))
sub = t[mask]
if len(sub):
    print("  통과분의 다음시가 수익률 보유:", int(sub["ret_next_open_pct"].notna().sum()))
    print("  통과분 세션별 건수(상위 5):", sub.groupby("date").size().sort_values(ascending=False).head(5).to_dict())
