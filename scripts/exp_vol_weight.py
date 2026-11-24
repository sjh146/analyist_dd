#!/usr/bin/env python3
"""변동성 가중치(W=0/15/45)가 일별 상위 20종목의 5일·1일 순수익에 미치는 영향 실험 — stdout 에 JSON 3줄.

kalman_momentum 은 패널 커버리지 2.5% 라 생산 trend 계열인 kalman_momentum_5d 를 쓴다.
"""

import json
import math
import os
import sys

import numpy as np
from scipy.stats import rankdata

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL_NAME = "panel_prod200_idn.npz"
TREND_F = "kalman_momentum_5d"
VOLUME_F = "volume_ratio_5"
VOL_F = "volatility_60d"
VOL_MU = 0.70
VOL_SIGMA = 0.40
TREND_PTS = 25.0
VOLUME_PTS = 30.0
TOP_N = 20
ROUND_TRIP_COST = 0.0021
MAX_ABS_FWD = 0.5
WEIGHTS = {"W0": 0.0, "W15": 15.0, "W45": 45.0}


def _panel_path():
    if os.path.isdir(os.path.join(PROJ, "app", "models", "wf")):
        return os.path.join(PROJ, "app", "models", "wf", PANEL_NAME)
    return os.path.join(PROJ, "services", "xgboost-ml", "app", "models", "wf", PANEL_NAME)


def load_panel(panel=None):
    if panel is None:
        panel = _panel_path()
    with np.load(panel) as p:
        names = [str(n) for n in p["feature_names"]]
        return (
            np.asarray(p["X"], dtype=np.float64),
            np.asarray(p["dates"]).astype(str),
            np.asarray(p["codes"]).astype(str),
            np.asarray(p["price"], dtype=np.float64),
            names,
        )


def _fwd(dates, codes, price):
    n = len(price)
    order = np.lexsort((dates, codes))
    p = price[order]
    c = codes[order]
    edges = np.flatnonzero(c[1:] != c[:-1]) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [n]))
    fwd1 = np.full(n, np.nan)
    fwd5 = np.full(n, np.nan)
    for s, e in zip(starts, ends, strict=True):
        if e - s > 5:
            fwd5[s : e - 5] = p[s + 5 : e] / p[s : e - 5] - 1.0
        if e - s > 1:
            fwd1[s : e - 1] = p[s + 1 : e] / p[s : e - 1] - 1.0
    fwd1[~np.isfinite(fwd1)] = np.nan
    fwd5[~np.isfinite(fwd5)] = np.nan
    inv = np.argsort(order)
    return fwd1[inv], fwd5[inv]


def _rank_z(col, s, e):
    x = col[s:e]
    valid = np.isfinite(x)
    z = np.full(x.shape, np.nan)
    if valid.sum() < 2:
        return z
    r = rankdata(x[valid], method="average")
    sd = float(r.std())
    z[valid] = (r - r.mean()) / sd if sd > 0.0 else 0.0
    return z


def measure(X, dates, codes, price, names, weight):
    n = X.shape[0]
    if not (len(dates) == n == len(codes) == len(price)):
        raise ValueError("X·dates·codes·price 행 수 불일치")
    missing = [f for f in (TREND_F, VOLUME_F, VOL_F) if f not in names]
    if missing:
        raise ValueError("패널에 피처 없음: %s" % missing)
    cols = [X[:, names.index(TREND_F)], X[:, names.index(VOLUME_F)]]
    v = X[:, names.index(VOL_F)]
    cols.append(np.exp(-0.5 * ((v - VOL_MU) / VOL_SIGMA) ** 2))
    order = np.argsort(dates, kind="stable")
    d = dates[order]
    cols = [c[order] for c in cols]
    fwd1, fwd5 = _fwd(dates, codes, price)
    f1 = fwd1[order]
    f5 = fwd5[order]
    starts = np.concatenate(([0], np.flatnonzero(d[1:] != d[:-1]) + 1))
    ends = np.concatenate((starts[1:], [n]))
    day_nets5 = []
    day_nets1 = []
    day_hits = []
    excluded = 0
    measured = 0
    for s, e in zip(starts, ends, strict=True):
        ok = np.ones(e - s, dtype=bool)
        zs = [_rank_z(col, s, e) for col in cols]
        for z in zs:
            ok &= np.isfinite(z)
        if not ok.any():
            continue
        score = np.full(e - s, np.nan)
        score[ok] = TREND_PTS * zs[0][ok] + VOLUME_PTS * zs[1][ok] + weight * zs[2][ok]
        sel = np.flatnonzero(ok)
        top = np.argsort(score[sel], kind="stable")[-min(TOP_N, len(sel)) :]
        picks = sel[top]
        f1p = f1[s + picks]
        f5p = f5[s + picks]
        broken = (np.abs(f1p) > MAX_ABS_FWD) | (np.abs(f5p) > MAX_ABS_FWD)
        excluded += int(broken.sum())
        good = np.isfinite(f1p) & np.isfinite(f5p) & ~broken
        if not good.any():
            continue
        day_nets5.append(float(np.mean(f5p[good]) - ROUND_TRIP_COST))
        day_nets1.append(float(np.mean(f1p[good]) - ROUND_TRIP_COST))
        day_hits.append(f5p[good] > 0.0)
        measured += int(good.sum())
    nets5 = np.asarray(day_nets5)
    nets1 = np.asarray(day_nets1)
    hits = np.concatenate(day_hits) if day_hits else np.asarray([], dtype=bool)
    days = len(nets5)
    sd5 = float(nets5.std(ddof=1)) if days > 1 else 0.0
    metrics = {
        "days": days,
        "picks_per_day": float(measured / days) if days else 0.0,
        "net5_mean": float(nets5.mean()) if days else 0.0,
        "net5_t": float(nets5.mean() / sd5 * math.sqrt(days)) if days > 1 and sd5 > 0.0 else 0.0,
        "net1_mean": float(nets1.mean()) if days else 0.0,
        "hit5": float(hits.mean()) if hits.size else 0.0,
        "excluded_rows": excluded,
    }
    return metrics, nets5


def main():
    panel = _panel_path()
    if not os.path.exists(panel):
        print("패널 없음: %s" % panel, file=sys.stderr)
        return 1
    try:
        X, dates, codes, price, names = load_panel(panel)
    except (OSError, ValueError) as exc:
        print("패널 읽기 실패(%s): %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    for tag, weight in WEIGHTS.items():
        metrics, _ = measure(X, dates, codes, price, names, weight)
        out = {"tag": tag}
        for key, value in metrics.items():
            out[key] = value if isinstance(value, int) else round(float(value), 6)
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
