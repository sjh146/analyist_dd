#!/usr/bin/env python3
"""label_wave4 — 승자 조합(h=5, 분위 q=0.3, top40, purge=5) 정밀 튜닝 + 시드 앙상블.

label_wave3 실측: purge 적용 후 h=5 → 0.5806(std 0.0063). 게이트 0.60까지 +0.019.
이 웨이브는 ① 시드 평균 예측 앙상블(분산 감소) ② 하이퍼 변형 ③ 선택 크기
④ 분위 임계 스윕을 같은 조건에서 비교한다.

시드 평균 앙상블: 각 시드 모델의 예측확률을 평균한 뒤 AUC 를 계산한다(단일 시드
AUC 평균보다 분산이 작아 일반적으로 더 높다).

실행: cd /app && OMP_NUM_THREADS=4 python -u scripts/label_wave4.py
결과: /app/reports/overnight/label_wave4.jsonl + label_wave4_summary.json
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

import train_curated as tc  # noqa: E402
import label_wave as lw  # noqa: E402
import label_wave3 as lw3  # noqa: E402

ml = lw.ml
OUT_DIR_ROOT = "/app/app/models/label_wave4"
RESULTS = "/app/reports/overnight/label_wave4.jsonl"
SUMMARY = "/app/reports/overnight/label_wave4_summary.json"

BASE = {"horizon": 5, "q": 0.3, "select": "top40",
        "recipe": {"lr": 0.03, "depth": 4, "n_estimators": 1500}}

CONFIGS = [
    {"id": "L1", **BASE, "seeds": 5, "desc": "기준 재현(h5 q0.3 top40 purge5)"},
    {"id": "L2", **BASE, "seeds": 10, "desc": "기준 + 10시드(평균예측 앙상블)"},
    {"id": "L3", **BASE, "seeds": 5, "recipe": {"lr": 0.02, "depth": 3, "n_estimators": 2000},
     "desc": "하이퍼 lr0.02 d3 est2000"},
    {"id": "L4", **BASE, "seeds": 5, "recipe": {"lr": 0.05, "depth": 5, "n_estimators": 1000},
     "desc": "하이퍼 lr0.05 d5 est1000"},
    {"id": "L5", **BASE, "seeds": 5, "select": "top20", "desc": "선택 top20"},
    {"id": "L6", **BASE, "seeds": 5, "select": "top30", "desc": "선택 top30"},
    {"id": "L7", **BASE, "seeds": 5, "select": "top50", "desc": "선택 top50"},
    {"id": "L8", **BASE, "seeds": 5, "q": 0.35, "desc": "분위 q=0.35"},
    {"id": "L9", **BASE, "seeds": 5, "q": 0.25, "desc": "분위 q=0.25"},
    {"id": "L10", **BASE, "seeds": 5, "horizon": 8, "desc": "8일 호라이즌 purge=8"},
]


def auc(y, p):
    from sklearn.metrics import roc_auc_score
    if len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, p))


def main():
    ml.set_exp_log("label_wave4")
    ml.log(f"label_wave4 start KST={ml.now_kst().isoformat(timespec='seconds')}")
    df, names = lw.load_panel()
    tc.select_curated_features = lambda n, a=False: list(n)
    base_names = [n for n in names if n in df.columns]

    results = []
    for cfg in CONFIGS:
        exp_id = cfg["id"]
        out_dir = os.path.join(OUT_DIR_ROOT, exp_id)
        os.makedirs(out_dir, exist_ok=True)
        recipe = cfg.get("recipe", BASE["recipe"])
        rec = {"exp": exp_id, "desc": cfg["desc"], "ts": ml.now_iso(), "status": "failed"}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            y = lw.make_labels(df, "quantile", cfg["horizon"], cfg["q"])
            split = lw3.split_purged(df, base_names, y, cfg["horizon"])
            if split is None:
                raise RuntimeError("split failed")
            X_tr, X_va, X_te, y_tr, y_va, y_te, fn = split
            idx, sel_desc = lw.subset(fn, cfg["select"], X_tr, y_tr)
            sel = [fn[i] for i in idx]
            ml.log(f"{exp_id} h={cfg['horizon']} q={cfg['q']} {sel_desc} | "
                   f"train={X_tr.shape} test={X_te.shape} up_rate={y_te.mean():.3f}")
            seed_aucs = {}
            probs = []
            for seed in range(cfg["seeds"]):
                a, m_aucs, cur, ens = ml.train_seed(
                    X_tr[:, idx], X_va[:, idx], X_te[:, idx],
                    y_tr, y_va, y_te, sel, out_dir, seed,
                    recipe["lr"], recipe["depth"], recipe["n_estimators"], True, None)
                seed_aucs[seed] = float(a)
                try:
                    p = ens.predict(X_te[:, idx])
                    p = np.asarray(p, dtype=float)
                    if p.ndim > 1:
                        p = p[:, -1]
                    probs.append(p)
                except Exception as e:
                    ml.log(f"  {exp_id} seed {seed} predict 실패: {e}")
                ml.log(f"  {exp_id} seed {seed}: test AUC={a:.4f}")
            vals = np.array(list(seed_aucs.values()))
            ens_auc = None
            if probs:
                ens_auc = auc(y_te, np.mean(np.vstack(probs), axis=0))
            rec.update({"status": "ok", "mean": float(vals.mean()),
                        "std": float(vals.std(ddof=1)), "seed_aucs": seed_aucs,
                        "ens_pred_auc": ens_auc, "n_features": len(sel),
                        "select": sel_desc, "n_test": int(len(y_te)),
                        "up_rate": float(y_te.mean()), "n_seeds": cfg["seeds"],
                        "recipe": recipe})
            ml.log(f"RESULT {exp_id} mean={rec['mean']:.4f} std={rec['std']:.4f} "
                   f"ens_pred_auc={ens_auc if ens_auc is None else round(ens_auc, 4)} "
                   f"features={len(sel)}")
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            ml.log(f"FAIL {exp_id}: {e}")
            ml.log(traceback.format_exc(), raw=True)
        results.append(rec)
        with open(RESULTS, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ok = [r for r in results if r.get("status") == "ok"]
    best = max(ok, key=lambda r: max(r["mean"], r.get("ens_pred_auc") or 0)) if ok else None
    with open(SUMMARY, "w") as f:
        json.dump({"finished_at": ml.now_iso(), "best": best, "results": results},
                  f, ensure_ascii=False, indent=2)
    ml.log(f"label_wave4 done. best={best['exp'] if best else None}")
    print(json.dumps({"all": [{"exp": r["exp"], "mean": r.get("mean"),
                               "std": r.get("std"),
                               "ens_pred_auc": r.get("ens_pred_auc"),
                               "desc": r["desc"]} for r in results]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
