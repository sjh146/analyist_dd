#!/usr/bin/env python3
"""승격 판정: AUC가 아니라 '비용 반영 기대값'으로 본다.

폴드마다 확장창 학습 → 테스트 구간 매일 예측 → 상위 K종목을 5거래일 보유했다고 가정하고
실현 수익에서 왕복 비용(기본 0.21%)을 뺀 순기대값을 계산한다. AUC도 같이 남긴다.

사용(컨테이너):
    docker exec -w /app stock_xgboost_ml python scripts/promotion_check.py \
        --panel /app/app/models/wf/panel_v8_sns_theme.npz --tag A --topk 3
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np


def load_panel(path):
    d = np.load(path, allow_pickle=True)
    return (np.asarray(d["X"], dtype=np.float32), [str(x) for x in d["feature_names"]],
            np.array([str(x) for x in d["dates"]]), np.array([str(x) for x in d["codes"]]),
            np.asarray(d["price"], dtype=np.float64))


def forward_returns(dates, codes, price, horizon=5):
    uniq = sorted(set(dates.tolist()))
    pos = {d: i for i, d in enumerate(uniq)}
    by_date = {}
    for i, d in enumerate(dates.tolist()):
        by_date.setdefault(d, []).append(i)
    out = np.full(len(dates), np.nan)
    for i, d in enumerate(dates.tolist()):
        k = pos[d]
        if k + horizon >= len(uniq):
            continue
        fut = {codes[j]: price[j] for j in by_date[uniq[k + horizon]]}
        p1 = fut.get(codes[i])
        if p1 and price[i] > 0:
            out[i] = p1 / price[i] - 1.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/app/app/models/wf/panel_v8_sns_theme.npz")
    ap.add_argument("--tag", default="A")
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--dates-per-fold", type=int, default=10)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--cost-pct", type=float, default=0.21)
    ap.add_argument("--dead-json", default="")
    args = ap.parse_args()

    X, names, dates, codes, price = load_panel(args.panel)
    fwd = forward_returns(dates, codes, price, args.horizon)
    keep = np.isfinite(fwd)
    X, dates, codes, fwd = X[keep], dates[keep], codes[keep], fwd[keep]
    if args.dead_json and os.path.exists(args.dead_json):
        dead = set(json.load(open(args.dead_json)))
        drop = [i for i, n in enumerate(names) if n in dead]
        X = np.delete(X, drop, axis=1)
        names = [n for i, n in enumerate(names) if i not in set(drop)]

    from sklearn.metrics import roc_auc_score
    import xgboost as xgb

    uniq = sorted(set(dates.tolist()))
    tail = uniq[-(args.folds * args.dates_per_fold):]
    chunks = [tail[i * args.dates_per_fold:(i + 1) * args.dates_per_fold] for i in range(args.folds)]
    aucs, nets, hits, picks = [], [], [], []
    for f, chunk in enumerate(chunks):
        first = min(chunk)
        idx_first = uniq.index(first)
        purge = set(uniq[max(0, idx_first - args.horizon):idx_first])
        tr = np.array([d < first and d not in purge for d in dates.tolist()])
        te = np.array([d in set(chunk) for d in dates.tolist()])
        if tr.sum() < 500 or te.sum() < 50:
            continue
        clf = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.05,
                                subsample=0.9, colsample_bytree=0.8, eval_metric="auc",
                                tree_method="hist", random_state=42)
        ytr = (fwd[tr] > 0).astype(int)
        clf.fit(X[tr], ytr)
        p = clf.predict_proba(X[te])[:, 1]
        yte = (fwd[te] > 0).astype(int)
        try:
            aucs.append(float(roc_auc_score(yte, p)))
        except ValueError:
            pass
        te_dates = dates[te]
        for d in chunk:
            m = te_dates == d
            if m.sum() < 20:
                continue
            order = np.argsort(-p[m])[:args.topk]
            rets = fwd[te][m][order]
            net = float(np.mean(rets)) * 100 - args.cost_pct
            nets.append(net)
            hits.append(float(np.mean(rets > 0)))
            picks.append(int(m.sum()))
        print("fold", f + 1, "test", int(te.sum()), "auc", round(aucs[-1], 4) if aucs else None,
              "누적 net 평균", round(float(np.mean(nets)), 3), "일수", len(nets))

    res = {
        "tag": args.tag,
        "features": int(X.shape[1]),
        "folds": len(chunks),
        "auc_mean": round(float(np.mean(aucs)), 4) if aucs else None,
        "net_expectancy_pct": round(float(np.mean(nets)), 3) if nets else None,
        "net_expectancy_t": round(float(np.mean(nets) / (np.std(nets) / np.sqrt(len(nets)) + 1e-12)), 2) if len(nets) > 1 else None,
        "hit_rate": round(float(np.mean(hits)), 3) if hits else None,
        "days": len(nets),
        "topk": args.topk,
        "cost_pct": args.cost_pct,
    }
    os.makedirs("/app/reports/promotion", exist_ok=True)
    with open(f"/app/reports/promotion/{args.tag}.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
