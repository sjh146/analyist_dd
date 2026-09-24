#!/usr/bin/env python3
"""label_wave2 — 승자 조합(2일 + 분위 라벨 + top40) 주변 정밀 튜닝.

label_wave 실측: 분위 라벨 +0.020, 2일 호라이즌 +0.029, 결합 0.5580(std 0.0028).
이 웨이브는 그 조합의 파라미터를 스윕한다(패널 캐시 재사용, 재빌드 없음).

실행: cd /app && OMP_NUM_THREADS=4 python -u scripts/label_wave2.py
결과: /app/reports/overnight/label_wave2.jsonl + label_wave2_summary.json
"""

import json
import logging
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import extra_experiments as ex  # noqa: E402
import train_curated as tc  # noqa: E402
import label_wave as lw  # noqa: E402  (헬퍼 재사용: load_panel/make_labels/subset)

ml = lw.ml
OUT_DIR_ROOT = "/app/app/models/label_wave2"
RESULTS = "/app/reports/overnight/label_wave2.jsonl"
SUMMARY = "/app/reports/overnight/label_wave2_summary.json"

BASE = {"label": "quantile", "horizon": 2, "q": 0.3, "select": "top40",
        "recipe": {"lr": 0.03, "depth": 4, "n_estimators": 1500}}

CONFIGS = [
    {"id": "J1", **BASE, "desc": "기준(2일+분위0.3+top40) 재현"},
    {"id": "J2", **BASE, "q": 0.2, "desc": "분위 q=0.2 (중간 60% 제외)"},
    {"id": "J3", **BASE, "q": 0.4, "desc": "분위 q=0.4 (중간 20% 제외)"},
    {"id": "J4", **BASE, "horizon": 4, "desc": "4일 호라이즌"},
    {"id": "J5", **BASE, "horizon": 5, "desc": "5일 호라이즌"},
    {"id": "J6", **BASE, "select": "top30", "desc": "선택 top30"},
    {"id": "J7", **BASE, "select": "top50", "desc": "선택 top50"},
    {"id": "J8", **BASE, "recipe": {"lr": 0.02, "depth": 3, "n_estimators": 2000},
     "desc": "하이퍼 lr0.02 d3 est2000"},
    {"id": "J9", **BASE, "recipe": {"lr": 0.05, "depth": 5, "n_estimators": 1000},
     "desc": "하이퍼 lr0.05 d5 est1000"},
    {"id": "J10", **BASE, "seeds": list(range(10)), "desc": "기준 조합 10시드(분산 확인)"},
]


def main():
    ml.set_exp_log("label_wave2")
    ml.log(f"label_wave2 start KST={ml.now_kst().isoformat(timespec='seconds')}")
    df, names = lw.load_panel()
    tc.select_curated_features = lambda n, a=False: list(n)

    results = []
    for cfg in CONFIGS:
        exp_id = cfg["id"]
        out_dir = os.path.join(OUT_DIR_ROOT, exp_id)
        os.makedirs(out_dir, exist_ok=True)
        seeds = cfg.get("seeds", lw.SEEDS)
        recipe = cfg.get("recipe", BASE["recipe"])
        rec = {"exp": exp_id, "desc": cfg["desc"], "ts": ml.now_iso(), "status": "failed"}
        ml.log(f"=== {exp_id}: {cfg['desc']} (seeds={len(seeds)}) ===")
        try:
            y = lw.make_labels(df, cfg["label"], cfg["horizon"], cfg.get("q", 0.3))
            split = ex._split_h(df, names, y)
            if split is None:
                raise RuntimeError("split failed")
            X_tr, X_va, X_te, y_tr, y_va, y_te, fn, dates = split
            idx, sel_desc = lw.subset(fn, cfg["select"], X_tr, y_tr)
            sel = [fn[i] for i in idx]
            ml.log(f"{exp_id} {cfg['label']} h={cfg['horizon']} q={cfg.get('q')} "
                   f"select={sel_desc} → {len(sel)}피처 test={X_te.shape} "
                   f"up_rate={y_te.mean():.3f}")
            seed_aucs = {}
            for seed in seeds:
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
                        "recipe": recipe, "n_seeds": len(seeds)})
            ml.log(f"RESULT {exp_id} mean={rec['mean']:.4f} std={rec['std']:.4f} "
                   f"features={len(sel)} up_rate={y_te.mean():.3f} seeds={len(seeds)}")
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
    ml.log(f"label_wave2 done. best={best['exp'] if best else None} "
           f"mean={best['mean'] if best else None}")
    print(json.dumps({"best": {"exp": best["exp"], "mean": best["mean"],
                               "std": best["std"], "desc": best["desc"]} if best else None,
                      "all": [{"exp": r["exp"], "mean": r.get("mean"),
                               "std": r.get("std"), "desc": r["desc"]}
                              for r in results]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
