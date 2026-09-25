#!/usr/bin/env python3
"""wf_wave — 장기 패널 빌드 + walk-forward(확장창) 교차검증.

배경: 지금까지의 AUC 는 고정 분할(60/20/20)에 따라 ±0.03 흔들렸다
(같은 설정이 0.6015 / 0.5704 / 0.5463). 또 패널이 49종목×180일 = 5,885행뿐이라
8일 호라이즌 라벨의 겹침을 감안하면 유효 표본이 더 작다.

이 스크립트는 두 가지를 한 번에 한다.
  1) 장기 패널 빌드(--days 420, 기본 캐시 /app/app/models/wf/panel_420.npz)
  2) 확장창 walk-forward: 날짜를 (folds+1)개 블록으로 나눠
     fold i → 학습 = 블록 0..i, 테스트 = 블록 i+1 (경계 h거래일 purge)
     각 fold 에서 피처 선택은 **그 fold 의 학습 구간에서만** 계산한다.

판정 기준은 fold 평균 AUC(그리고 fold 간 표준편차)다. 단일 분할 값은 쓰지 않는다.

실행(컨테이너):
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_wave.py            # 본 실행(2~3시간)
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_wave.py --smoke    # 경로 검증
결과: /app/reports/overnight/wf_wave.jsonl + wf_wave_summary.json
"""

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import extra_experiments as ex  # noqa: E402
import train_curated as tc  # noqa: E402

ml = ex._load_driver()
_ORIG_SELECT = tc.select_curated_features

BASE = {"horizon": 8, "q": 0.3, "select": "top30",
        "recipe": {"lr": 0.03, "depth": 4, "n_estimators": 1500}}

CONFIGS = [
    {"id": "WF1", **BASE, "desc": "승자: h8 + 분위0.3 + top30"},
    {"id": "WF2", **BASE, "select": "top40", "desc": "h8 + 분위0.3 + top40"},
    {"id": "WF3", **BASE, "horizon": 5, "desc": "h5 + 분위0.3 + top30"},
    {"id": "WF4", **BASE, "horizon": 6, "select": "top40", "desc": "h6 + top40 (분할 편차 최소)"},
    {"id": "WF5", **BASE, "recipe": {"lr": 0.02, "depth": 3, "n_estimators": 2000},
     "desc": "h8 + lr0.02 d3 est2000"},
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
    n1, n0 = int(yy.sum()), len(yy) - int(yy.sum())
    if n1 == 0 or n0 == 0:
        return 0.0
    return abs((ranks[yy == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0) - 0.5)


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
    return sorted(int(i) for i in order), f"top{k}"


def build_panel(cache, limit, days, log=print, **universe):
    """패널 캐시를 만들거나 재사용한다.

    universe: `tc._select_universe` 로 전달되는 확장 옵션(market/since/min_days/min_value/order).
    비우면 현행 기본값(KOSDAQ·코드순·최소 50일)이 그대로 쓰인다.
    ⚠ 캐시는 **파일명으로만** 구분된다 → 유니버스를 바꾸면 반드시 새 파일명을 써라
      (예: --panel /app/app/models/wf/panel_500.npz). 기존 패널을 덮으면 대조군이 사라진다.
    """
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        names = [str(n) for n in z["feature_names"]]
        df = pd.DataFrame(z["X"], columns=names)
        df["date"] = [str(d) for d in z["dates"]]
        df["stock_code"] = [str(c) for c in z["codes"]]
        df["price"] = z["price"].astype(float)
        log(f"panel cache 재사용: {df.shape} ({cache})")
        return df, names

    pg = ml.connect_pg()
    try:
        codes = tc._select_universe(pg, limit, **universe)
        log(f"universe: {len(codes)} 종목 (limit={limit})")
        pipeline = ml.FeaturePipeline(pg_conn=pg)
        end = datetime.now()
        start = end - timedelta(days=days)
        log(f"빌드 구간: {start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}")
        df = pipeline.build_training_features(
            codes, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        if df is None or len(df) < 100:
            raise RuntimeError("panel build failed")
        base = pipeline.get_feature_names()
        df, available = ml._engineer_features(df, base)
        log(f"panel: {df.shape} features={len(available)}")
    finally:
        try:
            pg.close()
        except Exception:
            pass

    os.makedirs(os.path.dirname(cache), exist_ok=True)
    np.savez_compressed(
        cache, X=df[available].values.astype(np.float32),
        feature_names=np.array(available),
        dates=df["date"].astype(str).values,
        codes=df["stock_code"].astype(str).values,
        price=df["price"].values.astype(np.float64))
    log(f"panel cache 저장: {cache}")
    return df, available


def make_labels(df, kind, horizon, q):
    ret = df.groupby("stock_code", sort=False)["price"].transform(
        lambda s: s.shift(-horizon) / s - 1.0)
    day = df["date"]
    if kind == "relative":
        med = ret.groupby(day).transform("median")
        y = (ret > med).astype(float)
        y[ret.isna()] = np.nan
        return y.values
    hi = ret.groupby(day).transform(lambda s: s.quantile(1 - q))
    lo = ret.groupby(day).transform(lambda s: s.quantile(q))
    y = pd.Series(np.nan, index=df.index, dtype=float)
    y[ret > hi] = 1.0
    y[ret < lo] = 0.0
    return y.values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=420)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()

    if args.smoke:
        args.days, args.limit, args.folds, args.seeds = 90, 8, 2, 1
        cache = "/app/app/models/wf/panel_smoke.npz"
        results_path = "/app/reports/overnight/wf_wave_smoke.jsonl"
        summary_path = "/app/reports/overnight/wf_wave_smoke_summary.json"
        cfgs = [dict(CONFIGS[0], horizon=3, select="top10")]
    else:
        cache = f"/app/app/models/wf/panel_{args.days}.npz"
        results_path = "/app/reports/overnight/wf_wave.jsonl"
        summary_path = "/app/reports/overnight/wf_wave_summary.json"
        cfgs = CONFIGS

    ml.set_exp_log("wf_wave")
    ml.log(f"wf_wave start KST={ml.now_kst().isoformat(timespec='seconds')} "
           f"days={args.days} limit={args.limit} folds={args.folds} seeds={args.seeds}")
    df, names = build_panel(cache, args.limit, args.days, log=ml.log)
    base_names = [n for n in names if n in df.columns]
    all_dates = sorted(df["date"].astype(str).unique())
    ml.log(f"panel rows={len(df)} dates={len(all_dates)} "
           f"({all_dates[0]} ~ {all_dates[-1]})")

    tc.select_curated_features = lambda n, a=False: list(n)

    results = []
    for cfg in cfgs:
        exp_id = cfg["id"]
        recipe = cfg.get("recipe", BASE["recipe"])
        out_dir = os.path.join("/app/app/models/wf", exp_id)
        os.makedirs(out_dir, exist_ok=True)
        rec = {"exp": exp_id, "desc": cfg["desc"], "ts": ml.now_iso(),
               "status": "failed", "folds": {}}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            y = make_labels(df, "quantile", cfg["horizon"], cfg["q"])
            d = df.copy()
            d["_y"] = y
            d = d[~pd.isna(d["_y"])]
            dd = sorted(d["date"].astype(str).unique())
            n = len(dd)
            step = n // (args.folds + 1)
            fold_means, fold_ens = [], []
            for i in range(1, args.folds + 1):
                cut = dd[step * i - 1]
                nxt = dd[min(n - 1, step * (i + 1) - 1)]
                h = cfg["horizon"]
                purge = set(dd[max(0, step * i - h):step * i])
                tr = d[(d["date"] <= cut) & (~d["date"].isin(purge))]
                te = d[(d["date"] > cut) & (d["date"] <= nxt)]
                if min(len(tr), len(te)) < 100:
                    ml.log(f"  {exp_id} fold{i}: 표본 부족(tr={len(tr)} te={len(te)}), 건너뜀")
                    continue
                Xtr = np.nan_to_num(tr[base_names].values.astype(np.float32), nan=0.0)
                ytr = tr["_y"].values.astype(int)
                Xte = np.nan_to_num(te[base_names].values.astype(np.float32), nan=0.0)
                yte = te["_y"].values.astype(int)
                cols = np.std(Xtr, axis=0) > 0
                fn = [f for f, m in zip(base_names, cols) if m]
                Xtr, Xte = Xtr[:, cols], Xte[:, cols]
                idx, sel_desc = subset(fn, cfg["select"], Xtr, ytr)
                sel = [fn[j] for j in idx]
                aucs, probs = [], []
                for seed in range(args.seeds):
                    a, m_aucs, cur, ens = ml.train_seed(
                        Xtr[:, idx], None, Xte[:, idx], ytr, None, yte, sel,
                        out_dir, seed, recipe["lr"], recipe["depth"],
                        recipe["n_estimators"], True, None)
                    aucs.append(float(a))
                    try:
                        p = np.asarray(ens.predict(Xte[:, idx]), dtype=float)
                        probs.append(p[:, -1] if p.ndim > 1 else p)
                    except Exception:
                        pass
                ens_auc = None
                if probs:
                    from sklearn.metrics import roc_auc_score
                    ens_auc = float(roc_auc_score(yte, np.mean(np.vstack(probs), axis=0)))
                fold_means.append(float(np.mean(aucs)))
                if ens_auc:
                    fold_ens.append(ens_auc)
                rec["folds"][f"fold{i}"] = {
                    "train_rows": int(len(ytr)), "test_rows": int(len(yte)),
                    "test_from": str(te["date"].min()), "test_to": str(te["date"].max()),
                    "mean": float(np.mean(aucs)), "std": float(np.std(aucs, ddof=1)),
                    "ens_pred_auc": ens_auc, "n_features": len(sel),
                    "up_rate": float(np.mean(yte))}
                ml.log(f"  {exp_id} fold{i}: train={len(ytr)} test={len(yte)} "
                       f"({te['date'].min()}~{te['date'].max()}) mean={np.mean(aucs):.4f} "
                       f"ens={ens_auc if ens_auc is None else round(ens_auc, 4)} "
                       f"feat={len(sel)}")
            if fold_means:
                rec.update({"status": "ok", "fold_mean": float(np.mean(fold_means)),
                            "fold_std": float(np.std(fold_means, ddof=1)),
                            "fold_min": float(np.min(fold_means)),
                            "fold_max": float(np.max(fold_means)),
                            "ens_mean": float(np.mean(fold_ens)) if fold_ens else None,
                            "n_folds_run": len(fold_means)})
                ml.log(f"RESULT {exp_id} fold평균={rec['fold_mean']:.4f} "
                       f"±{rec['fold_std']:.4f} (min {rec['fold_min']:.4f} / "
                       f"max {rec['fold_max']:.4f}) ens평균="
                       f"{rec['ens_mean'] if rec['ens_mean'] is None else round(rec['ens_mean'], 4)}")
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            ml.log(f"FAIL {exp_id}: {e}")
            ml.log(traceback.format_exc(), raw=True)
        results.append(rec)
        with open(results_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ok = [r for r in results if r.get("status") == "ok"]
    best = max(ok, key=lambda r: r["fold_mean"]) if ok else None
    with open(summary_path, "w") as f:
        json.dump({"finished_at": ml.now_iso(), "config": vars(args),
                   "best": best, "results": results}, f, ensure_ascii=False, indent=2)
    ml.log(f"wf_wave done. best={best['exp'] if best else None} "
           f"fold_mean={best['fold_mean'] if best else None}")
    print(json.dumps({"all": [{"exp": r["exp"], "fold_mean": r.get("fold_mean"),
                               "fold_std": r.get("fold_std"),
                               "fold_min": r.get("fold_min"),
                               "fold_max": r.get("fold_max"),
                               "ens_mean": r.get("ens_mean"),
                               "desc": r["desc"]} for r in results]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
