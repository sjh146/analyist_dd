#!/usr/bin/env python3
"""label_wave5 — 0.6 도전: 호라이즌 정밀 스윕 + **분할 강건성 검증**.

label_wave4 실측: h=8 + 분위 q=0.3 + top40 + purge=8 → mean 0.5961 / 평균예측 0.5983.
테스트 행이 686개뿐이라 분할(60/20/20) 선택에 따라 값이 흔들릴 수 있다.
이 웨이브는 각 설정을 **3개 분할**(60/20/20, 70/15/15, 50/25/25)에서 평가해
분할 간 평균±표준편차를 보고한다 — 특정 분할에 과적합된 값을 걸러내기 위함이다.

purge 는 호라이즌만큼 경계 직전 거래일을 제거한다(누수 차단).

실행: cd /app && OMP_NUM_THREADS=4 python -u scripts/label_wave5.py
결과: /app/reports/overnight/label_wave5.jsonl + label_wave5_summary.json
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

import train_curated as tc  # noqa: E402
import label_wave as lw  # noqa: E402

ml = lw.ml
OUT_DIR_ROOT = "/app/app/models/label_wave5"
RESULTS = "/app/reports/overnight/label_wave5.jsonl"
SUMMARY = "/app/reports/overnight/label_wave5_summary.json"
SPLITS = [(0.60, 0.80), (0.70, 0.85), (0.50, 0.75)]
SEEDS = list(range(5))

BASE = {"horizon": 8, "q": 0.3, "select": "top40",
        "recipe": {"lr": 0.03, "depth": 4, "n_estimators": 1500}}

CONFIGS = [
    {"id": "M1", **BASE, "desc": "승자(h8 q0.3 top40) 재현"},
    {"id": "M2", **BASE, "select": "top30", "desc": "h8 + top30"},
    {"id": "M3", **BASE, "q": 0.35, "desc": "h8 + q0.35"},
    {"id": "M4", **BASE, "horizon": 6, "desc": "h6 + top40"},
    {"id": "M5", **BASE, "horizon": 10, "desc": "h10 + top40"},
    {"id": "M6", **BASE, "horizon": 12, "desc": "h12 + top40"},
    {"id": "M7", **BASE, "recipe": {"lr": 0.05, "depth": 5, "n_estimators": 1000},
     "desc": "h8 + hyper lr0.05 d5 est1000"},
    {"id": "M8", **BASE, "recipe": {"lr": 0.02, "depth": 3, "n_estimators": 2000},
     "desc": "h8 + hyper lr0.02 d3 est2000"},
    {"id": "M9", **BASE, "q": 0.2, "desc": "h8 + q0.2"},
    {"id": "M10", **BASE, "horizon": 15, "desc": "h15 + top40"},
]


def split_at(df, names, y, horizon, f1, f2):
    d = df.copy()
    d["_y"] = y
    d["_d"] = d["date"].astype(str)
    d = d[~pd.isna(d["_y"])]
    dates = sorted(d["_d"].unique())
    n = len(dates)
    i1, i2 = int(n * f1), int(n * f2)
    b1, b2 = dates[i1 - 1], dates[i2 - 1]
    p_tr = set(dates[max(0, i1 - horizon):i1])
    p_va = set(dates[max(0, i2 - horizon):i2])
    tr = d[(d["_d"] <= b1) & (~d["_d"].isin(p_tr))]
    va = d[(d["_d"] > b1) & (d["_d"] <= b2) & (~d["_d"].isin(p_va))]
    te = d[d["_d"] > b2]
    if min(len(tr), len(va), len(te)) < 50:
        return None
    out = []
    for part in (tr, va, te):
        X = np.nan_to_num(part[names].values.astype(np.float32), nan=0.0)
        out.append((X, part["_y"].values.astype(int)))
    Xtr, ytr = out[0]
    cols = np.std(Xtr, axis=0) > 0
    keep = [f for f, m in zip(names, cols) if m]
    return (Xtr[:, cols], out[1][0][:, cols], out[2][0][:, cols],
            ytr, out[1][1], out[2][1], keep)


def main():
    ml.set_exp_log("label_wave5")
    ml.log(f"label_wave5 start KST={ml.now_kst().isoformat(timespec='seconds')}")
    df, names = lw.load_panel()
    tc.select_curated_features = lambda n, a=False: list(n)
    base_names = [n for n in names if n in df.columns]

    results = []
    for cfg in CONFIGS:
        exp_id = cfg["id"]
        recipe = cfg.get("recipe", BASE["recipe"])
        out_dir = os.path.join(OUT_DIR_ROOT, exp_id)
        os.makedirs(out_dir, exist_ok=True)
        rec = {"exp": exp_id, "desc": cfg["desc"], "ts": ml.now_iso(),
               "status": "failed", "per_split": {}}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            y = lw.make_labels(df, "quantile", cfg["horizon"], cfg["q"])
            split_means = []
            for f1, f2 in SPLITS:
                sp = split_at(df, base_names, y, cfg["horizon"], f1, f2)
                if sp is None:
                    ml.log(f"  {exp_id} split {f1}/{f2}: 표본 부족, 건너뜀")
                    continue
                X_tr, X_va, X_te, y_tr, y_va, y_te, fn = sp
                idx, sel_desc = lw.subset(fn, cfg["select"], X_tr, y_tr)
                sel = [fn[i] for i in idx]
                aucs = []
                probs = []
                for seed in SEEDS:
                    a, m_aucs, cur, ens = ml.train_seed(
                        X_tr[:, idx], X_va[:, idx], X_te[:, idx],
                        y_tr, y_va, y_te, sel, out_dir, seed,
                        recipe["lr"], recipe["depth"], recipe["n_estimators"], True, None)
                    aucs.append(float(a))
                    try:
                        p = np.asarray(ens.predict(X_te[:, idx]), dtype=float)
                        probs.append(p[:, -1] if p.ndim > 1 else p)
                    except Exception:
                        pass
                ens_auc = None
                if probs:
                    from sklearn.metrics import roc_auc_score
                    ens_auc = float(roc_auc_score(y_te, np.mean(np.vstack(probs), axis=0)))
                key = f"{f1:.2f}/{f2:.2f}"
                rec["per_split"][key] = {
                    "mean": float(np.mean(aucs)), "std": float(np.std(aucs, ddof=1)),
                    "ens_pred_auc": ens_auc, "n_test": int(len(y_te)),
                    "n_features": len(sel), "select": sel_desc,
                    "up_rate": float(np.mean(y_te))}
                split_means.append(float(np.mean(aucs)))
                ml.log(f"  {exp_id} split={key} mean={np.mean(aucs):.4f} "
                       f"std={np.std(aucs, ddof=1):.4f} ens={ens_auc} "
                       f"test={len(y_te)} feat={len(sel)}")
            if split_means:
                rec.update({"status": "ok", "split_mean": float(np.mean(split_means)),
                            "split_std": float(np.std(split_means, ddof=1))
                            if len(split_means) > 1 else 0.0,
                            "split_min": float(np.min(split_means)),
                            "split_max": float(np.max(split_means))})
                ml.log(f"RESULT {exp_id} 분할평균={rec['split_mean']:.4f} "
                       f"±{rec['split_std']:.4f} (min {rec['split_min']:.4f} / "
                       f"max {rec['split_max']:.4f})")
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            ml.log(f"FAIL {exp_id}: {e}")
            ml.log(traceback.format_exc(), raw=True)
        results.append(rec)
        with open(RESULTS, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ok = [r for r in results if r.get("status") == "ok"]
    best = max(ok, key=lambda r: r["split_mean"]) if ok else None
    with open(SUMMARY, "w") as f:
        json.dump({"finished_at": ml.now_iso(), "best": best, "results": results},
                  f, ensure_ascii=False, indent=2)
    ml.log(f"label_wave5 done. best={best['exp'] if best else None} "
           f"split_mean={best['split_mean'] if best else None}")
    print(json.dumps({"all": [{"exp": r["exp"], "split_mean": r.get("split_mean"),
                               "split_std": r.get("split_std"),
                               "split_min": r.get("split_min"),
                               "split_max": r.get("split_max"),
                               "desc": r["desc"]} for r in results]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
