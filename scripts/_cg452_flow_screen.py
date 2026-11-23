"""수급·공매도 계열 피처의 커버리지와 h5 횡단면 단변량 AUC 를 청정 패널에서 잰다.

왜: 이 피처들을 90% 커버리지로 백필하는 야간 작업(수집 역할 승인 필요)을 요청할 가치가
있는지 먼저 판정한다. 백필은 병목이 '커버리지'일 때만 의미가 있고, 비영 표본에서도
|AUC-0.5| 가 0.02 미만이면 신호가 없는 축이므로 종결한다.
"""
import collections
import sys

import numpy as np
from scipy.stats import rankdata

PANEL = "/app/app/models/wf/panel_prod200_v6o.npz"
HORIZON = 5
KEYS = (
    "foreign_net_buy",
    "foreign_net_buy_5d",
    "institution_net_buy",
    "institution_net_buy_5d",
    "foreign_ownership_pct",
    "institution_ownership_pct",
    "program_trading_ratio",
    "short_selling_ratio",
    "short_interest_ratio",
    "credit_balance_change",
    "etf_flow_5d",
    "return_5d",
)


def auc(y, s):
    m = np.isfinite(s) & np.isfinite(y)
    if m.sum() < 500:
        return None, int(m.sum())
    yy = y[m]
    n1 = yy.sum()
    n0 = m.sum() - n1
    if n1 < 10 or n0 < 10:
        return None, int(m.sum())
    r = rankdata(s[m])
    return float((r[yy == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)), int(m.sum())


def forward_label(dates, codes, price):
    n = len(dates)
    fwd = np.full(n, np.nan)
    by_code = collections.defaultdict(list)
    for i, c in enumerate(codes):
        by_code[c].append(i)
    for ii in by_code.values():
        ii.sort(key=lambda i: dates[i])
        pr = price[ii]
        for j in range(len(ii) - HORIZON):
            if pr[j] > 0:
                fwd[ii[j]] = pr[j + HORIZON] / pr[j] - 1.0
    lab = np.full(n, np.nan)
    for dt in np.unique(dates):
        ii = np.where(dates == dt)[0]
        v = fwd[ii]
        fin = np.isfinite(v)
        if fin.sum() > 3:
            lab[ii] = v - np.nanmedian(v[fin])
    return lab


def main(path=PANEL):
    d = np.load(path, allow_pickle=True)
    fn = [str(x) for x in d["feature_names"]]
    X = d["X"].astype(float)
    dates = np.array([str(x) for x in d["dates"]])
    codes = np.array([str(x) for x in d["codes"]])
    price = d["price"].astype(float)
    lab = forward_label(dates, codes, price)
    y = (lab > 0).astype(float)
    print(f"{path} rows={len(X)} feats={len(fn)} dates={len(np.unique(dates))} labeled={int(np.isfinite(lab).sum())}")
    print(f"{'feature':26s} {'cov':>6s} {'auc_all':>8s} {'auc_nonzero':>11s} {'n_nonzero':>9s}")
    for k in KEYS:
        if k not in fn:
            print(f"{k:26s} ABSENT")
            continue
        s = X[:, fn.index(k)]
        nz = np.isfinite(s) & (s != 0)
        a_all, n_all = auc(y, s)
        a_nz, n_nz = auc(y[nz], s[nz])
        fmt = lambda v: "   n/a" if v is None else f"{v:8.4f}"
        print(f"{k:26s} {nz.mean():6.3f} {fmt(a_all)} {fmt(a_nz)} {n_nz:9d}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
