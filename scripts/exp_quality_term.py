#!/usr/bin/env python3
"""퀄리티·유사주 항을 단타 스코어에 더하는 실험 — stdout 에 JSON 4줄(A/B/C/D) + A 대비 페어드 t.

기준 = kalman_momentum_5d(40) + volume_ratio_5(45) + vol_fit(15, volatility_60d 가우시안 근사).
A: 추가 없음. B: quality_score +20. C: quality_score +40.
D: 기준을 60% 로 축소(24/27/9)한 뒤 quality_score +20, similar_stocks_return_avg +20 — 합 100.
모든 항목 날짜별 횡단면 rank-z 점수.
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
QUALITY_F = "quality_score"
SIMILAR_F = "similar_stocks_return_avg"
VOL_MU = 0.70
VOL_SIGMA = 0.40
TOP_N = 20
ROUND_TRIP_COST = 0.0021
MAX_ABS_FWD = 0.5
VARIANTS = {
    "A": (40.0, 45.0, 15.0, 0.0, 0.0),
    "B": (40.0, 45.0, 15.0, 20.0, 0.0),
    "C": (40.0, 45.0, 15.0, 40.0, 0.0),
    "D": (24.0, 27.0, 9.0, 20.0, 20.0),
}


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


def measure(X, dates, codes, price, names, weights):
    n = X.shape[0]
    if not (len(dates) == n == len(codes) == len(price)):
        raise ValueError("X·dates·codes·price 행 수 불일치")
    missing = [f for f in (TREND_F, VOLUME_F, VOL_F, QUALITY_F, SIMILAR_F) if f not in names]
    if missing:
        raise ValueError("패널에 피처 없음: %s" % missing)
    cols = [X[:, names.index(TREND_F)], X[:, names.index(VOLUME_F)]]
    v = X[:, names.index(VOL_F)]
    cols.append(np.exp(-0.5 * ((v - VOL_MU) / VOL_SIGMA) ** 2))
    cols.append(X[:, names.index(QUALITY_F)])
    cols.append(X[:, names.index(SIMILAR_F)])
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
    day_map = {}
    excluded = 0
    measured = 0
    for s, e in zip(starts, ends, strict=True):
        ok = np.ones(e - s, dtype=bool)
        zs = []
        for idx, col in enumerate(cols):
            z = _rank_z(col, s, e)
            zs.append(z)
            if weights[idx] != 0.0:
                ok &= np.isfinite(z)
        if not ok.any():
            continue
        score = np.full(e - s, np.nan)
        score[ok] = sum(w * z[ok] for w, z in zip(weights, zs, strict=True))
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
        net5 = float(np.mean(f5p[good]) - ROUND_TRIP_COST)
        net1 = float(np.mean(f1p[good]) - ROUND_TRIP_COST)
        day_nets5.append(net5)
        day_nets1.append(net1)
        day_hits.append(f5p[good] > 0.0)
        day_map[str(d[s])] = (net1, net5)
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
    return metrics, day_map


def _paired_t(day_map_a, day_map_v):
    common = sorted(set(day_map_a) & set(day_map_v))
    if len(common) < 2:
        return {"n_days": len(common), "t1": 0.0, "t5": 0.0}
    d1 = np.asarray([day_map_v[k][0] - day_map_a[k][0] for k in common])
    d5 = np.asarray([day_map_v[k][1] - day_map_a[k][1] for k in common])
    t1 = float(d1.mean() / d1.std(ddof=1) * math.sqrt(len(common)))
    t5 = float(d5.mean() / d5.std(ddof=1) * math.sqrt(len(common)))
    return {
        "n_days": len(common),
        "net1_diff_mean": float(d1.mean()),
        "t1": t1,
        "net5_diff_mean": float(d5.mean()),
        "t5": t5,
    }


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
    results = {}
    for tag, weights in VARIANTS.items():
        metrics, day_map = measure(X, dates, codes, price, names, weights)
        results[tag] = day_map
        out = {"tag": tag}
        for key, value in metrics.items():
            out[key] = value if isinstance(value, int) else round(float(value), 6)
        print(json.dumps(out, ensure_ascii=False))
    for tag in ("B", "C", "D"):
        paired = _paired_t(results["A"], results[tag])
        out = {"pair": "%s-vs-A" % tag}
        for key, value in paired.items():
            out[key] = value if isinstance(value, int) else round(float(value), 6)
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
