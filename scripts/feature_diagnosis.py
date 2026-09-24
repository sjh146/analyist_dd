#!/usr/bin/env python3
"""feature_diagnosis — Q1 패널에서 피처별 단변량 예측력(단일피처 AUC / 순위상관)을 측정한다.

목적: "뉴스·이벤트·매크로·SNS 피처를 채웠는데 왜 AUC 가 오르지 않는가"를
숫자로 답한다. 모델을 다시 학습하지 않고, 학습에 실제로 쓰인 패널
(panel_cache.npz)만 사용한다.
"""

import json
import sys

import numpy as np

PANEL = sys.argv[1] if len(sys.argv) > 1 else "/app/app/models/news_wave/Q1/panel_cache.npz"

GROUPS = {
    "sentiment_live": ("sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d",
                       "sentiment_avg_db", "sentiment_trend", "sentiment_volatility",
                       "sentiment_momentum", "news_count_5d", "news_count_20d"),
    "news_event": ("event_", "market_impact_score", "theme_exposure_5d"),
    "sns": ("sns_",),
    "macro": ("fx_", "oil_", "interest_rate", "cpi_yoy", "ppi_yoy",
              "yield_spread", "credit_spread", "economic_event"),
    "fundamental": ("value_", "quality_", "momentum_ni", "momentum_op", "per_current",
                    "pbr_current", "roe", "per_percentile", "pbr_percentile"),
    "graph_theme": ("theme_count", "theme_max_relevance", "theme_momentum",
                    "twin_count", "twin_avg_correlation", "cycle_up", "cycle_down"),
    "market_breadth": ("market_breadth", "krx_advance_decline_ratio", "relative_strength",
                       "krx_total_trading_value"),
    "price_tech": ("ma_position", "return_", "volatility_", "volume_ratio", "atr",
                   "bb_position", "rsi", "macd", "price", "momentum_", "kalman_",
                   "trend_", "cross_trend", "volume_price", "rank_", "target_ma_"),
}


def auc_single(x, y):
    """단일 피처 AUC (Mann-Whitney U). 값이 모두 같으면 None."""
    m = ~np.isnan(x)
    x, y = x[m], y[m]
    if len(x) < 30 or len(np.unique(x)) < 2:
        return None
    if y.min() == y.max():
        return None
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    # tie 평균
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return None
    r1 = ranks[y == 1].sum()
    return float((r1 - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def main():
    d = np.load(PANEL, allow_pickle=True)
    names = [str(n) for n in d["feature_names"]]
    X = np.vstack([d["X_train"], d["X_val"], d["X_test"]]).astype(float)
    y = np.concatenate([d["y_train"], d["y_val"], d["y_test"]]).astype(int)
    print(f"[panel] X={X.shape} y_up_rate={y.mean():.4f} (n_pos={int(y.sum())})")

    rows = []
    for i, n in enumerate(names):
        col = X[:, i]
        nz = float(np.mean(col != 0))
        sd = float(np.std(col))
        a = auc_single(col, y)
        rows.append({"feature": n, "nonzero": nz, "std": sd, "auc": a,
                     "edge": (abs(a - 0.5) if a is not None else None)})

    dead = [r for r in rows if r["std"] == 0]
    live = [r for r in rows if r["std"] > 0]
    print(f"[features] {len(rows)}개 | 상수(std=0) {len(dead)}개 | 변동 있음 {len(live)}개")

    scored = [r for r in live if r["auc"] is not None]
    scored.sort(key=lambda r: -r["edge"])
    print("\n[단일피처 AUC 상위 20 — |AUC-0.5| 기준]")
    for r in scored[:20]:
        print(f"  {r['feature']:34s} AUC={r['auc']:.4f} edge={r['edge']:.4f} "
              f"nz={r['nonzero']:.3f}")

    print("\n[그룹별 단변량 예측력] 그룹: 피처수 / 평균edge / 최고edge(피처)")
    for g, pats in GROUPS.items():
        sel = [r for r in scored
               if any(r["feature"] == p or r["feature"].startswith(p) for p in pats)]
        if not sel:
            continue
        edges = np.array([r["edge"] for r in sel])
        best = max(sel, key=lambda r: r["edge"])
        print(f"  {g:15s} {len(sel):3d} / mean {edges.mean():.4f} / "
              f"max {best['edge']:.4f} ({best['feature']}, AUC={best['auc']:.4f})")

    # 라벨과 완전히 같은(누수) 피처 점검
    leak = [r for r in scored if r["auc"] is not None and r["auc"] > 0.75]
    print(f"\n[누수 의심] 단일피처 AUC > 0.75: {len(leak)}개 "
          f"{[r['feature'] for r in leak][:10]}")

    out = "/app/reports/feature_diagnosis.json"
    with open(out, "w") as f:
        json.dump({"panel": PANEL, "n_features": len(rows), "n_dead": len(dead),
                   "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n[saved] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
