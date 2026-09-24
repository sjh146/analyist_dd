#!/usr/bin/env python3
"""label_wave — 라벨 설계 · 피처 선택 크기 스윕 (패널 캐시 재사용, 재빌드 없음).

근거(2026-09-24 selected_wave 실측):
  * 절대(1일) 라벨 → 시장상대 라벨: +0.014 (0.5133 → 0.5270)
  * 시장상대 + 학습구간 edge 상위 40피처: 0.5305 (밤 최고)
  * 전체 피처(F6 0.5267) ≈ curated(F2 0.5270) → 피처 희석은 주범이 아님.
→ 다음 가설: 라벨을 더 깎으면(분위·위험조정·다중일) 오른다.

패널 캐시(/app/app/models/selected/panel_full.npz)를 읽어 재학습만 한다.
피처 선택은 항상 **학습 구간에서만** 계산한다(테스트 누수 금지).

실행: cd /app && OMP_NUM_THREADS=4 python -u scripts/label_wave.py
결과: /app/reports/overnight/label_wave.jsonl + label_wave_summary.json
"""

import json
import logging
import os
import sys
import traceback

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import extra_experiments as ex  # noqa: E402
import train_curated as tc  # noqa: E402

ml = ex._load_driver()
_ORIG_SELECT = tc.select_curated_features

PANEL_CACHE = "/app/app/models/selected/panel_full.npz"
OUT_DIR_ROOT = "/app/app/models/label_wave"
RESULTS = "/app/reports/overnight/label_wave.jsonl"
SUMMARY = "/app/reports/overnight/label_wave_summary.json"
SEEDS = [0, 1, 2, 3, 4]
RECIPE = {"lr": 0.03, "depth": 4, "n_estimators": 1500}

CONFIGS = [
    {"id": "G1", "label": "relative", "horizon": 1, "select": "top40",
     "desc": "기준: 시장상대 + top40 (F3 재현)"},
    {"id": "G2", "label": "quantile", "horizon": 1, "select": "top40", "q": 0.3,
     "desc": "분위 라벨(상위30%=1/하위30%=0, 중간 제외) + top40"},
    {"id": "G3", "label": "risk_adj", "horizon": 1, "select": "top40",
     "desc": "위험조정 라벨(초과수익/횡단면 변동성) + top40"},
    {"id": "G4", "label": "relative", "horizon": 2, "select": "top40",
     "desc": "2일 호라이즌 시장상대 라벨 + top40"},
    {"id": "G5", "label": "relative", "horizon": 3, "select": "top40",
     "desc": "3일 호라이즌 시장상대 라벨 + top40"},
    {"id": "G6", "label": "quantile", "horizon": 2, "select": "top40", "q": 0.3,
     "desc": "2일 + 분위 라벨 + top40"},
    {"id": "G7", "label": "relative", "horizon": 1, "select": "top20",
     "desc": "시장상대 + top20 (선택 크기 스윕)"},
    {"id": "G8", "label": "relative", "horizon": 1, "select": "top60",
     "desc": "시장상대 + top60"},
    {"id": "G9", "label": "quantile", "horizon": 1, "select": "top60", "q": 0.2,
     "desc": "분위 라벨(상위20%/하위20%) + top60"},
    {"id": "G10", "label": "relative", "horizon": 1, "select": "top40",
     "recipe": {"lr": 0.02, "depth": 3, "n_estimators": 2000},
     "desc": "시장상대 + top40 + 하이퍼(lr0.02 d3 est2000)"},
]


def edge_of(col, y):
    m = ~np.isnan(col)
    x, yy = col[m], y[m]
    if len(x) < 50 or len(np.unique(x)) < 2 or yy.min() == yy.max():
        return 0.0
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    n1 = int(yy.sum())
    n0 = len(yy) - n1
    if n1 == 0 or n0 == 0:
        return 0.0
    return abs((ranks[yy == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0) - 0.5)


def load_panel():
    z = np.load(PANEL_CACHE, allow_pickle=True)
    names = [str(n) for n in z["feature_names"]]
    df = pd.DataFrame(z["X"], columns=names)
    df["date"] = [str(d) for d in z["dates"]]
    df["stock_code"] = [str(c) for c in z["codes"]]
    df["price"] = z["price"].astype(float)
    ml.log(f"panel cache loaded: {df.shape} features={len(names)}")
    return df, names


def next_ret(df, horizon=1):
    return df.groupby("stock_code", sort=False)["price"].transform(
        lambda s: s.shift(-horizon) / s - 1.0)


def make_labels(df, kind, horizon, q=0.3):
    ret = next_ret(df, horizon)
    day = df["date"]
    if kind == "relative":
        med = ret.groupby(day).transform("median")
        y = (ret > med).astype(float)
        y[ret.isna()] = np.nan
        return y.values
    if kind == "quantile":
        hi = ret.groupby(day).transform(lambda s: s.quantile(1 - q))
        lo = ret.groupby(day).transform(lambda s: s.quantile(q))
        y = pd.Series(np.nan, index=df.index, dtype=float)
        y[ret > hi] = 1.0
        y[ret < lo] = 0.0
        return y.values
    if kind == "risk_adj":
        med = ret.groupby(day).transform("median")
        sd = ret.groupby(day).transform("std")
        excess = ret - med
        y = (excess / sd.replace(0.0, np.nan) > 0).astype(float)
        y[ret.isna()] = np.nan
        return y.values
    raise ValueError(kind)


def subset(names, select, X_train, y_train):
    if select == "all":
        return list(range(len(names))), "all"
    if select.startswith("curated"):
        keep = set(_ORIG_SELECT(names, select.endswith("48")))
        return [i for i, n in enumerate(names) if n in keep], select
    edges = np.array([edge_of(X_train[:, i].astype(float), y_train)
                      for i in range(len(names))])
    k = int(select.replace("top", ""))
    order = np.argsort(-edges)[:k]
    return sorted(int(i) for i in order), f"top{k}(min edge {edges[order[-1]]:.4f})"


def main():
    ml.set_exp_log("label_wave")
    ml.log(f"label_wave start KST={ml.now_kst().isoformat(timespec='seconds')}")
    df, names = load_panel()
    tc.select_curated_features = lambda n, a=False: list(n)

    results = []
    for cfg in CONFIGS:
        exp_id = cfg["id"]
        out_dir = os.path.join(OUT_DIR_ROOT, exp_id)
        os.makedirs(out_dir, exist_ok=True)
        rec = {"exp": exp_id, "desc": cfg["desc"], "ts": ml.now_iso(), "status": "failed"}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            y = make_labels(df, cfg["label"], cfg["horizon"], cfg.get("q", 0.3))
            n_valid = int(np.sum(~np.isnan(y)))
            split = ex._split_h(df, names, y)
            if split is None:
                raise RuntimeError("split failed")
            X_tr, X_va, X_te, y_tr, y_va, y_te, fn, dates = split
            idx, sel_desc = subset(fn, cfg["select"], X_tr, y_tr)
            sel = [fn[i] for i in idx]
            recipe = cfg.get("recipe", RECIPE)
            ml.log(f"{exp_id} label={cfg['label']} h={cfg['horizon']} "
                   f"select={sel_desc} → {len(sel)}피처 | test={X_te.shape} "
                   f"up_rate={y_te.mean():.3f} (valid rows {n_valid})")
            seed_aucs = {}
            for seed in SEEDS:
                auc, m_aucs, cur, ens = ml.train_seed(
                    X_tr[:, idx], X_va[:, idx], X_te[:, idx],
                    y_tr, y_va, y_te, sel, out_dir, seed,
                    recipe["lr"], recipe["depth"], recipe["n_estimators"], True, None)
                seed_aucs[seed] = float(auc)
                ml.log(f"  {exp_id} seed {seed}: test AUC={auc:.4f}")
            vals = np.array(list(seed_aucs.values()))
            rec.update({"status": "ok", "mean": float(vals.mean()),
                        "std": float(vals.std(ddof=1)), "seed_aucs": seed_aucs,
                        "n_features": len(sel), "select": sel_desc,
                        "n_rows": int(len(y_te)), "up_rate": float(y_te.mean()),
                        "recipe": recipe})
            ml.log(f"RESULT {exp_id} mean={rec['mean']:.4f} std={rec['std']:.4f} "
                   f"features={len(sel)} up_rate={y_te.mean():.3f}")
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            ml.log(f"FAIL {exp_id}: {e}")
            ml.log(traceback.format_exc(), raw=True)
        results.append(rec)
        with open(RESULTS, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ok = [r for r in results if r.get("status") == "ok"]
    best = max(ok, key=lambda r: r["mean"]) if ok else None
    with open(SUMMARY, "w") as f:
        json.dump({"finished_at": ml.now_iso(), "best": best, "results": results},
                  f, ensure_ascii=False, indent=2)
    ml.log(f"label_wave done. best={best['exp'] if best else None} "
           f"mean={best['mean'] if best else None}")
    print(json.dumps({"best": {"exp": best["exp"], "mean": best["mean"],
                               "std": best["std"], "desc": best["desc"]} if best else None,
                      "all": [{"exp": r["exp"], "mean": r.get("mean"),
                               "std": r.get("std"),
                               "label": r.get("up_rate")} for r in results]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
