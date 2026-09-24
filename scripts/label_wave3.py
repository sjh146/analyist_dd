#!/usr/bin/env python3
"""label_wave3 — 호라이즌 스윕 + **purge(경계 제거)** 로 누수 검증.

배경: label_wave2 에서 5일 호라이즌 + 분위 라벨이 0.5855 로 급등했다.
그러나 라벨이 "다음 h 거래일 수익률"이면, 시간순 60/20/20 분할에서 **경계 직전
학습/검증 행의 라벨이 검증/테스트 구간의 가격을 사용**하게 되어 AUC 가 부풀 수 있다.

이 스크립트는 각 블록의 마지막 h개 거래일 행을 **버려서(purge)** 누수를 차단하고,
purge 전/후를 같은 조건에서 비교해 상승분이 진짜인지 판정한다.

실행: cd /app && OMP_NUM_THREADS=4 python -u scripts/label_wave3.py
결과: /app/reports/overnight/label_wave3.jsonl + label_wave3_summary.json
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

ml = lw.ml
OUT_DIR_ROOT = "/app/app/models/label_wave3"
RESULTS = "/app/reports/overnight/label_wave3.jsonl"
SUMMARY = "/app/reports/overnight/label_wave3_summary.json"

CONFIGS = [
    # purge 없음(기존 방식) — 누수 크기 측정용
    {"id": "K1", "horizon": 1, "q": 0.3, "purge": False, "seeds": 5, "desc": "h=1 purge없음(대조)"},
    {"id": "K2", "horizon": 5, "q": 0.3, "purge": False, "seeds": 5, "desc": "h=5 purge없음(누수 의심)"},
    # purge 적용 — 진짜 성능
    {"id": "K3", "horizon": 1, "q": 0.3, "purge": True, "seeds": 5, "desc": "h=1 purge=1"},
    {"id": "K4", "horizon": 2, "q": 0.3, "purge": True, "seeds": 5, "desc": "h=2 purge=2"},
    {"id": "K5", "horizon": 3, "q": 0.3, "purge": True, "seeds": 5, "desc": "h=3 purge=3"},
    {"id": "K6", "horizon": 5, "q": 0.3, "purge": True, "seeds": 5, "desc": "h=5 purge=5"},
    {"id": "K7", "horizon": 10, "q": 0.3, "purge": True, "seeds": 5, "desc": "h=10 purge=10"},
    {"id": "K8", "horizon": 5, "q": 0.4, "purge": True, "seeds": 5, "desc": "h=5 q=0.4 purge=5"},
    {"id": "K9", "horizon": 10, "q": 0.4, "purge": True, "seeds": 5, "desc": "h=10 q=0.4 purge=10"},
    {"id": "K10", "horizon": 5, "q": 0.4, "purge": True, "seeds": 10, "desc": "h=5 q=0.4 purge=10시드"},
]


def split_purged(df, names, y, horizon):
    """시간순 60/20/20 + 각 블록의 마지막 h 거래일 행 제거(purge).

    purge 는 '라벨이 다음 h일 가격을 쓰는' 문제를 경계에서 차단한다.
    """
    import pandas as pd

    d = df.copy()
    d["_y"] = y
    d["_d"] = d["date"].astype(str)
    d = d[~pd.isna(d["_y"])]
    dates = sorted(d["_d"].unique())
    n = len(dates)
    b1 = dates[int(n * 0.60) - 1]
    b2 = dates[int(n * 0.80) - 1]
    # 경계 직전 h 거래일은 라벨이 다음 블록 가격을 포함 → 제거
    purged_train = set(dates[max(0, int(n * 0.60) - horizon):int(n * 0.60)])
    purged_val = set(dates[max(0, int(n * 0.80) - horizon):int(n * 0.80)])
    tr = d[(d["_d"] <= b1) & (~d["_d"].isin(purged_train))]
    va = d[(d["_d"] > b1) & (d["_d"] <= b2) & (~d["_d"].isin(purged_val))]
    te = d[d["_d"] > b2]

    out = []
    for part in (tr, va, te):
        X = part[names].values.astype(np.float32)
        X = np.nan_to_num(X, nan=0.0)
        out.append((X, part["_y"].values.astype(int)))
    Xtr, ytr = out[0]
    Xva, yva = out[1]
    Xte, yte = out[2]
    if min(len(ytr), len(yva), len(yte)) < 50:
        return None
    cols = np.std(Xtr, axis=0) > 0
    keep = [f for f, m in zip(names, cols) if m]
    return (Xtr[:, cols], Xva[:, cols], Xte[:, cols], ytr, yva, yte, keep)


def main():
    ml.set_exp_log("label_wave3")
    ml.log(f"label_wave3 start KST={ml.now_kst().isoformat(timespec='seconds')}")
    df, names = lw.load_panel()
    tc.select_curated_features = lambda n, a=False: list(n)
    base_names = [n for n in names if n in df.columns]

    results = []
    for cfg in CONFIGS:
        exp_id = cfg["id"]
        out_dir = os.path.join(OUT_DIR_ROOT, exp_id)
        os.makedirs(out_dir, exist_ok=True)
        rec = {"exp": exp_id, "desc": cfg["desc"], "ts": ml.now_iso(),
               "status": "failed", "purge": cfg["purge"], "horizon": cfg["horizon"]}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            y = lw.make_labels(df, "quantile", cfg["horizon"], cfg["q"])
            if cfg["purge"]:
                split = split_purged(df, base_names, y, cfg["horizon"])
            else:
                import extra_experiments as ex
                split = ex._split_h(df, base_names, y)
            if split is None:
                raise RuntimeError("split failed")
            X_tr, X_va, X_te, y_tr, y_va, y_te, fn = split[:7]
            idx, sel_desc = lw.subset(fn, "top40", X_tr, y_tr)
            sel = [fn[i] for i in idx]
            ml.log(f"{exp_id} h={cfg['horizon']} q={cfg['q']} purge={cfg['purge']} "
                   f"{sel_desc} | train={X_tr.shape} val={X_va.shape} test={X_te.shape} "
                   f"up_rate={y_te.mean():.3f}")
            seed_aucs = {}
            for seed in range(cfg["seeds"]):
                auc, m_aucs, cur, ens = ml.train_seed(
                    X_tr[:, idx], X_va[:, idx], X_te[:, idx],
                    y_tr, y_va, y_te, sel, out_dir, seed, 0.03, 4, 1500, True, None)
                seed_aucs[seed] = float(auc)
                ml.log(f"  {exp_id} seed {seed}: test AUC={auc:.4f}")
            vals = np.array(list(seed_aucs.values()))
            rec.update({"status": "ok", "mean": float(vals.mean()),
                        "std": float(vals.std(ddof=1)), "seed_aucs": seed_aucs,
                        "n_features": len(sel), "select": sel_desc,
                        "n_test": int(len(y_te)), "n_train": int(len(y_tr)),
                        "up_rate": float(y_te.mean()), "n_seeds": cfg["seeds"]})
            ml.log(f"RESULT {exp_id} mean={rec['mean']:.4f} std={rec['std']:.4f} "
                   f"test_rows={len(y_te)}")
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
    ml.log(f"label_wave3 done. best={best['exp'] if best else None} "
           f"mean={best['mean'] if best else None}")
    print(json.dumps({"best": {"exp": best["exp"], "mean": best["mean"],
                               "std": best["std"], "desc": best["desc"]} if best else None,
                      "all": [{"exp": r["exp"], "mean": r.get("mean"),
                               "std": r.get("std"), "purge": r.get("purge"),
                               "h": r.get("horizon")} for r in results]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
