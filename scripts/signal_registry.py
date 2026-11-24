#!/usr/bin/env python3
"""신호 레지스트리 — 피처별로 '비용을 넘는 돈을 버는 신호'인지 실측해 alive/weak/dead 로 남긴다.

날짜별 횡단면 Spearman IC(h1/h5/h20)와 h5 상위 10% 예측 종목의 평균 순수익(왕복 0.21% 차감)을
패널에서 직접 계산한다. 읽기 전용 — numpy/scipy 만 사용, DB·네트워크 없음.
"""
import argparse
import json
import math
import os
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
from scipy.stats import rankdata

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

HORIZONS = (1, 5, 20)
ROUND_TRIP_COST = 0.0021
MIN_PAIRS = 10
TOP_FRAC = 0.10
ALIVE_IC = 0.010
ALIVE_T = 2.0
WEAK_IC = 0.005
WEAK_T = 1.5
MIN_DAYS = 60
MAX_ABS_FWD = 0.5

REQUIRED_KEYS = ("X", "feature_names", "dates", "codes", "price")
PANEL_NAME = "panel_prod200_idn.npz"


def _r6(x):
    if x is None:
        return None
    f = float(x)
    return None if not math.isfinite(f) else round(f, 6)


def classify(ic_h5, t_h5, n_days=None):
    """판정 규칙 + 표본일수 부족은 판정 보류(insufficient)."""
    if n_days is not None and n_days < MIN_DAYS:
        return "insufficient"
    a = abs(ic_h5)
    b = abs(t_h5)
    if a >= ALIVE_IC and b >= ALIVE_T:
        return "alive"
    if a >= WEAK_IC or b >= WEAK_T:
        return "weak"
    return "dead"


def _fwd_returns(dates, codes, price):
    n = len(price)
    order = np.lexsort((dates, codes))
    p = price[order]
    c = codes[order]
    edges = np.flatnonzero(c[1:] != c[:-1]) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [n]))
    inv = np.argsort(order)
    out = {}
    for h in HORIZONS:
        fwd = np.full(n, np.nan)
        for s, e in zip(starts, ends, strict=True):
            if e - s > h:
                fwd[s:e - h] = p[s + h:e] / p[s:e - h] - 1.0
        fwd[~np.isfinite(fwd)] = np.nan
        fwd[np.abs(fwd) > MAX_ABS_FWD] = np.nan
        out[h] = fwd[inv]
    return out


def _date_blocks(dates):
    order = np.argsort(dates, kind="stable")
    d = dates[order]
    starts = np.concatenate(([0], np.flatnonzero(d[1:] != d[:-1]) + 1))
    ends = np.concatenate((starts[1:], [len(d)]))
    return order, starts, ends


def _daily_ic(x, y, starts, ends):
    ics = []
    for s, e in zip(starts, ends, strict=True):
        xs = x[s:e]
        ys = y[s:e]
        mask = ~np.isnan(xs) & ~np.isnan(ys)
        if int(mask.sum()) < MIN_PAIRS:
            continue
        rx = rankdata(xs[mask])
        ry = rankdata(ys[mask])
        if rx.max() == rx.min() or ry.max() == ry.min():
            continue
        ics.append(float(np.corrcoef(rx, ry)[0, 1]))
    return np.asarray(ics)


def _top10_net(x, y, starts, ends):
    nets = []
    for s, e in zip(starts, ends, strict=True):
        xs = x[s:e]
        ys = y[s:e]
        mask = ~np.isnan(xs) & ~np.isnan(ys)
        if int(mask.sum()) < MIN_PAIRS:
            continue
        k = max(1, int(np.ceil(TOP_FRAC * mask.sum())))
        top = np.argsort(xs[mask], kind="stable")[-k:]
        nets.append(float(np.mean(ys[mask][top])) - ROUND_TRIP_COST)
    return np.asarray(nets)


def _ic_stats(ics):
    n = len(ics)
    if n == 0:
        return None, None, None
    mean = float(np.mean(ics))
    if n < 2:
        return mean, None, None
    std = float(np.std(ics, ddof=1))
    if std <= 0.0:
        return mean, std, None
    return mean, std, mean / std * math.sqrt(n)


def _rel_returns(fwd, order, starts, ends):
    rel = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        for h in HORIZONS:
            y = fwd[h][order]
            meds = np.array([np.nanmedian(y[s:e]) for s, e in zip(starts, ends, strict=True)])
            rel[h] = y - np.repeat(meds, ends - starts)
    return rel


def compute_registry(X, feature_names, dates, codes, price, max_features=None, on_progress=None):
    """패널 배열 → 판정 payload(rows·features·summary). generated_at 은 main 이 붙인다."""
    X = np.asarray(X, dtype=np.float64)
    dates = np.asarray(dates).astype(str)
    codes = np.asarray(codes).astype(str)
    price = np.asarray(price, dtype=np.float64)
    names = [str(n) for n in feature_names]
    n = X.shape[0]
    if not (len(dates) == n == len(codes) == len(price)):
        raise ValueError("X·dates·codes·price 행 수 불일치")
    if len(names) != X.shape[1]:
        raise ValueError("feature_names 수(%d) != X 열 수(%d)" % (len(names), X.shape[1]))
    m = X.shape[1] if max_features is None else min(X.shape[1], max_features)
    fwd = _fwd_returns(dates, codes, price)
    order, starts, ends = _date_blocks(dates)
    rel = _rel_returns(fwd, order, starts, ends)
    Xd = X[order][:, :m]
    t0 = time.monotonic()
    features = []
    for j in range(m):
        col = Xd[:, j]
        daily = {h: _daily_ic(col, rel[h], starts, ends) for h in HORIZONS}
        stats = {h: _ic_stats(daily[h]) for h in HORIZONS}
        ic_h5 = stats[5][0]
        t_h5 = stats[5][2]
        fwd5 = fwd[5][order]
        nets = _top10_net(col, fwd5, starts, ends)
        excess = _top10_net(col, rel[5], starts, ends)
        features.append({
            "name": names[j],
            "ic_h1": _r6(stats[1][0]),
            "ic_h5": _r6(ic_h5),
            "ic_h20": _r6(stats[20][0]),
            "t_h5": _r6(t_h5),
            "coverage": _r6(float(np.mean(~np.isnan(col)))),
            "top10_net_h5": _r6(float(np.mean(nets))) if len(nets) else None,
            "top10_excess_h5": _r6(float(np.mean(excess))) if len(excess) else None,
            "status": classify(ic_h5 if ic_h5 is not None else float("nan"),
                               t_h5 if t_h5 is not None else float("nan"),
                               len(daily[5])),
            "n_days": int(len(daily[5])),
        })
        if on_progress is not None and (j + 1) % 25 == 0:
            on_progress(j + 1, m, time.monotonic() - t0)
    summary = {"alive": 0, "weak": 0, "dead": 0, "insufficient": 0}
    for f in features:
        summary[f["status"]] += 1
    return {"rows": int(n), "features": features, "summary": summary}


def _defaults():
    if os.path.isdir(os.path.join(PROJ, "app", "models", "wf")):
        panel = os.path.join(PROJ, "app", "models", "wf", PANEL_NAME)
        out_dir = "/tmp"
    else:
        panel = os.path.join(PROJ, "services", "xgboost-ml", "app", "models", "wf", PANEL_NAME)
        out_dir = os.path.join(PROJ, "data", "reports")
    return panel, os.path.join(out_dir, "signal_registry.json"), os.path.join(out_dir, "signal_registry.md")


def _fmt_ic(v):
    return "-" if v is None else "%.4f" % v


def _fmt_t(v):
    return "-" if v is None else "%.2f" % v


def _fmt_net(v):
    return "-" if v is None else "%+.3f%%" % (v * 100.0)


def _render_md(payload, panel_path):
    sm = payload["summary"]
    feats = payload["features"]
    L = []
    add = L.append
    add("# 신호 레지스트리")
    add("")
    add("- 패널: `%s` — rows=%d features=%d" % (panel_path, payload["rows"], len(feats)))
    add("- 생성: %s" % payload["generated_at"])
    add("- 판정 임계: alive |IC_h5|>=%.3f & |t_h5|>=%.1f · weak |IC_h5|>=%.3f | |t_h5|>=%.1f · dead 그 외"
        % (ALIVE_IC, ALIVE_T, WEAK_IC, WEAK_T))
    add("- 비용: 왕복 %.2f%% (상위 10%% 예측 종목 평균 수익에서 차감) · IC: 날짜별 횡단면 Spearman, "
        "유효쌍>=%d · t=mean/std*sqrt(n_days)" % (ROUND_TRIP_COST * 100.0, MIN_PAIRS))
    add("- 판정: alive %d · weak %d · dead %d · insufficient(표본<%d일) %d (총 %d)"
        % (sm["alive"], sm["weak"], sm["dead"], MIN_DAYS, sm.get("insufficient", 0), len(feats)))
    add("- 데이터 이상치: |전방수익|>%.0f%% 행은 IC·순수익 계산에서 제외(액면분할·단절 오염 방지)" % (MAX_ABS_FWD * 100))
    add("")
    ranked = [f for f in feats if f["status"] != "insufficient"]
    top = sorted(ranked,
                 key=lambda f: (abs(f["ic_h5"] if f["ic_h5"] is not None else 0.0), f["name"]),
                 reverse=True)[:25]
    add("## |IC_h5| 상위 25")
    add("")
    add("| # | feature | IC_h5 | t_h5 | coverage | top10_net_h5 | top10_excess_h5 | status |")
    add("|---|---|---|---|---|---|---|---|")
    for i, f in enumerate(top, 1):
        add("| %d | %s | %s | %s | %.3f | %s | %s | %s |"
            % (i, f["name"], _fmt_ic(f["ic_h5"]), _fmt_t(f["t_h5"]), f["coverage"],
               _fmt_net(f["top10_net_h5"]), _fmt_net(f.get("top10_excess_h5")), f["status"]))
    add("")
    pos = [f for f in ranked if f["top10_net_h5"] is not None and f["top10_net_h5"] > 0.0]
    pos.sort(key=lambda f: f["top10_net_h5"], reverse=True)
    add("## 상위 10%% 순수익이 플러스인 피처 (%d)" % len(pos))
    add("")
    for f in pos:
        add("- `%s` (%s)" % (f["name"], _fmt_net(f["top10_net_h5"])))
    add("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="신호 레지스트리 — 피처별 비용 대비 수익 판정")
    panel_default, json_default, md_default = _defaults()
    ap.add_argument("--panel", default=panel_default)
    ap.add_argument("--json-out", default=json_default)
    ap.add_argument("--md-out", default=md_default)
    ap.add_argument("--max-features", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(args.panel):
        print("패널 없음: %s" % args.panel, file=sys.stderr)
        return 1
    try:
        with np.load(args.panel) as p:
            missing = [k for k in REQUIRED_KEYS if k not in p.files]
            if missing:
                print("패널에 필수 키 없음: %s (실제: %s)" % (missing, sorted(p.files)),
                      file=sys.stderr)
                return 1
            X = p["X"]
            feature_names = p["feature_names"]
            dates = p["dates"]
            codes = p["codes"]
            price = p["price"]
    except (OSError, ValueError) as e:
        print("패널 읽기 실패(%s): %s" % (type(e).__name__, e), file=sys.stderr)
        return 1

    t_start = time.monotonic()
    payload = compute_registry(
        X, feature_names, dates, codes, price,
        max_features=args.max_features,
        on_progress=lambda j, m, el: print("[signal_registry] %d/%d 피처 %.1fs 경과" % (j, m, el)))
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for path in (args.json_out, args.md_out):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(args.json_out, "w", encoding="utf-8") as f:
        f.write(json_text)
    os.chmod(args.json_out, 0o644)
    with open(args.md_out, "w", encoding="utf-8") as f:
        f.write(_render_md(payload, args.panel))
    os.chmod(args.md_out, 0o644)

    if not args.quiet:
        sm = payload["summary"]
        print("[signal_registry] panel=%s rows=%d features=%d alive=%d weak=%d dead=%d elapsed=%.1fs"
              % (args.panel, payload["rows"], len(payload["features"]),
                 sm["alive"], sm["weak"], sm["dead"], time.monotonic() - t_start))
        top5 = sorted(payload["features"],
                      key=lambda f: (abs(f["ic_h5"] if f["ic_h5"] is not None else 0.0), f["name"]),
                      reverse=True)[:5]
        for f in top5:
            print("  top5 %-33s ic_h5=%s t_h5=%s top10_net_h5=%s status=%s"
                  % (f["name"], f["ic_h5"], f["t_h5"], f["top10_net_h5"], f["status"]))
        print("[signal_registry] json -> %s | md -> %s" % (args.json_out, args.md_out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
