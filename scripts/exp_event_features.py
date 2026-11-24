#!/usr/bin/env python3
"""신규 이벤트 피처 7종의 기여도 측정 — 패널에 이벤트 컬럼을 붙여 WF 비교.

  docker exec -w /app -e PGPASSWORD=... stock_xgboost_ml python /app/scripts/exp_event_features.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, "/app/scripts")
import exp_panel_wf as base  # noqa: E402

EVENT_COLS = [
    "event_shareholder_meeting_5d", "event_ir_5d", "event_dividend_5d",
    "event_debt_guarantee_5d", "event_option_grant_5d", "event_investment_5d",
    "event_rumor_5d",
]


def load_events(codes: list[str], dates: list[str]) -> dict:
    import psycopg2
    conn = psycopg2.connect(host="postgres", port=5432, user="stock_user",
                            password=os.environ["PGPASSWORD"], dbname="stock_trading")
    cur = conn.cursor()
    cur.execute(
        "SELECT stock_code, trade_date::text, {0} FROM event_features "
        "WHERE trade_date >= %s AND trade_date <= %s".format(", ".join(EVENT_COLS)),
        (min(dates), max(dates)))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {(r[0], r[1]): r[2:] for r in rows}


def run(tag: str, X, names, dates, codes, y):
    fold_aucs = []
    uniq = sorted(set(dates))
    tail = uniq[-50:]
    chunks = [tail[i * 10:(i + 1) * 10] for i in range(5)]
    pooled_y, pooled_p = [], []
    for chunk in chunks:
        first = min(chunk)
        pos = uniq.index(first)
        purge = set(uniq[max(0, pos - 5):pos])
        test_mask = np.array([d in set(chunk) for d in dates])
        train_mask = np.array([d < first and d not in purge for d in dates])
        if train_mask.sum() < 500 or test_mask.sum() < 50:
            continue
        p = base.fit_predict(X[train_mask], y[train_mask], X[test_mask])
        if p is None:
            continue
        fold_aucs.append(float(np.mean(base.auc_cs(dates[test_mask], y[test_mask], p))))
        pooled_y.append(y[test_mask])
        pooled_p.append(p)
    from sklearn.metrics import roc_auc_score
    pooled = float(roc_auc_score(np.concatenate(pooled_y), np.concatenate(pooled_p)))
    result = {"tag": tag, "features": int(X.shape[1]), "folds": len(fold_aucs),
              "fold_aucs": [round(a, 4) for a in fold_aucs],
              "fold_mean": round(float(np.mean(fold_aucs)), 4),
              "fold_std": round(float(np.std(fold_aucs)), 4),
              "auc_pooled": round(pooled, 4)}
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    X, names, dates, codes, price = base.load_panel()
    y_raw, _ = base.make_labels(dates, codes, price)
    keep = ~np.isnan(y_raw)
    X, y, dates, codes = X[keep], (y_raw[keep] > 0).astype(int), dates[keep], codes[keep]

    base_res = run("panel_only", X, names, dates, codes, y)

    events = load_events(sorted(set(codes.tolist())), sorted(set(dates.tolist())))
    add = np.zeros((len(dates), len(EVENT_COLS)), dtype=np.float32)
    for i, (c, d) in enumerate(zip(codes, dates)):
        vals = events.get((c, d))
        if vals:
            add[i] = [float(v or 0) for v in vals]
    cov = {EVENT_COLS[j]: round(float((add[:, j] > 0).mean()), 4) for j in range(len(EVENT_COLS))}
    print("event coverage", cov)
    X2 = np.concatenate([X, add], axis=1)
    ev_res = run("panel_plus_events", X2, names, dates, codes, y)

    out = {"base": base_res, "with_events": ev_res, "event_coverage": cov,
           "delta_fold_mean": round(ev_res["fold_mean"] - base_res["fold_mean"], 4)}
    json.dump(out, open("/app/reports/exp_event_features.json", "w"), ensure_ascii=False, indent=2)
    print("delta", out["delta_fold_mean"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
