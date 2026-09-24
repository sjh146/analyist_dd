#!/usr/bin/env python3
"""selected_wave — 피처 선택 + 라벨 설계 결합 실험.

배경(실측 2026-09-24):
  * 피처를 199개로 부활시킨 뒤 전체 피처로 5시드 학습 → AUC 0.4945 (부활 전 0.5112보다 악화).
  * 단변량 예측력: 감성·이벤트·SNS 계열 edge 평균 0.001~0.003 (무정보),
    매크로 0.035, 테마/사이클 0.016, 시장폭 0.016.
  * 밤 최고 기록은 시장상대(cross-sectional) 라벨 H3 = 0.5323.
  → 가설: (1) 노이즈 피처 희석이 악화의 원인, (2) 라벨 설계가 신호의 원천.

이 스크립트는 패널을 **한 번만** 빌드해 재사용하고(빌드가 70분 소요),
라벨 × 피처선택 조합을 순차 평가한다. 피처 선택은 **학습 구간에서만** 계산한다
(테스트 정보 누수 금지).

실행(컨테이너): cd /app && OMP_NUM_THREADS=4 python -u scripts/selected_wave.py
결과: /app/reports/overnight/selected_wave.jsonl + selected_wave_summary.json
      패널 캐시: /app/app/models/selected/panel_full.npz
"""

import json
import os
import sys
import traceback
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import extra_experiments as ex  # noqa: E402
import train_curated as tc  # noqa: E402

# 3rd-party 로거(피처 파이프라인 등)의 진행 로그를 stdout 으로 보이게 한다.
import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ml = ex._load_driver()

# 학습 서브셋을 직접 지정하기 위해 원본 curated 선택 함수를 보존한다.
_ORIG_SELECT = tc.select_curated_features

OUT_DIR_ROOT = "/app/app/models/selected"
PANEL_CACHE = os.path.join(OUT_DIR_ROOT, "panel_full.npz")
RESULTS = "/app/reports/overnight/selected_wave.jsonl"
SUMMARY = "/app/reports/overnight/selected_wave_summary.json"

SEEDS = [0, 1, 2, 3, 4]
LIMIT = 50
DAYS = 180

CONFIGS = [
    {"id": "F1", "label": "abs", "select": "curated43", "horizon": 1,
     "desc": "기준선 재현: 절대(1일) 라벨 + curated43"},
    {"id": "F2", "label": "relative", "select": "curated43", "horizon": 1,
     "desc": "H3 재현: 시장상대 라벨 + curated43"},
    {"id": "F3", "label": "relative", "select": "top40", "horizon": 1,
     "desc": "시장상대 라벨 + 학습구간 edge 상위 40피처"},
    {"id": "F4", "label": "relative", "select": "edge0.01", "horizon": 1,
     "desc": "시장상대 라벨 + 학습구간 edge>=0.01 피처"},
    {"id": "F5", "label": "abs", "select": "top40", "horizon": 1,
     "desc": "절대(1일) 라벨 + 학습구간 edge 상위 40피처"},
    {"id": "F6", "label": "relative", "select": "all", "horizon": 1,
     "desc": "시장상대 라벨 + 전체 피처(희석 대조군)"},
]


def edge_of(col, y):
    """단변량 |AUC-0.5| (학습 구간에서만 호출)."""
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
    r1 = ranks[yy == 1].sum()
    auc = (r1 - n1 * (n1 + 1) / 2.0) / (n1 * n0)
    return abs(auc - 0.5)


def build_panel():
    """패널을 1회 빌드하고 캐시에 저장. (df, available, feature_names) 반환."""
    import pandas as pd

    if os.path.exists(PANEL_CACHE):
        z = np.load(PANEL_CACHE, allow_pickle=True)
        names = [str(n) for n in z["feature_names"]]
        df = pd.DataFrame(z["X"], columns=names)
        df["date"] = [str(d) for d in z["dates"]]
        df["stock_code"] = [str(c) for c in z["codes"]]
        if "price" in z:
            df["price"] = z["price"]
        ml.log(f"panel cache 재사용: {df.shape}")
        return df, names

    pg = ml.connect_pg()
    try:
        codes = tc._select_universe(pg, LIMIT)
        ml.log(f"universe: {len(codes)} 종목 (limit={LIMIT})")
        pipeline = ml.FeaturePipeline(pg_conn=pg)
        end = datetime.now()
        start = end - timedelta(days=DAYS)
        df = pipeline.build_training_features(
            codes, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        if df is None or len(df) < 100:
            raise RuntimeError("panel build failed")
        base = pipeline.get_feature_names()
        df, available = ml._engineer_features(df, base)
        ml.log(f"panel: {df.shape} features={len(available)}")
    finally:
        try:
            pg.close()
        except Exception:
            pass

    os.makedirs(OUT_DIR_ROOT, exist_ok=True)
    X = df[available].values.astype(np.float32)
    np.savez_compressed(
        PANEL_CACHE, X=X, feature_names=np.array(available),
        dates=df["date"].astype(str).values,
        codes=df["stock_code"].astype(str).values,
        price=df["price"].values.astype(np.float64) if "price" in df else np.zeros(len(df)),
    )
    ml.log(f"panel cache 저장: {PANEL_CACHE} {X.shape}")
    return df, available


def make_labels(df, label, horizon):
    if label == "relative":
        return ex.market_relative_labels(df, horizon=horizon)
    return ml._create_labels_horizon(df, horizon=horizon)


def subset_indices(names, select, X_train, y_train):
    if select == "curated43":
        keep = set(_ORIG_SELECT(names, False))
        return [i for i, n in enumerate(names) if n in keep], "curated43"
    if select == "curated48":
        keep = set(_ORIG_SELECT(names, True))
        return [i for i, n in enumerate(names) if n in keep], "curated48"
    if select == "all":
        return list(range(len(names))), "all"
    edges = np.array([edge_of(X_train[:, i].astype(float), y_train)
                      for i in range(len(names))])
    if select == "top40":
        order = np.argsort(-edges)[:40]
        return sorted(int(i) for i in order), f"top40(train edge>= {edges[order[-1]]:.4f})"
    if select.startswith("edge"):
        thr = float(select.replace("edge", ""))
        idx = [i for i in range(len(names)) if edges[i] >= thr]
        if len(idx) < 10:
            idx = sorted(int(i) for i in np.argsort(-edges)[:10])
        return idx, f"edge>={thr} ({len(idx)}개)"
    raise ValueError(select)


def main():
    global SEEDS, LIMIT, DAYS, PANEL_CACHE
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=LIMIT)
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--seeds", default=",".join(str(s) for s in SEEDS))
    ap.add_argument("--only", default="", help="쉼표 구분 실험 id")
    ap.add_argument("--smoke", action="store_true", help="소형 패널로 경로 검증")
    args = ap.parse_args()
    LIMIT, DAYS = args.limit, args.days
    SEEDS = [int(s) for s in args.seeds.split(",") if s.strip()]

    cfgs = CONFIGS
    if args.only:
        want = {x.strip() for x in args.only.split(",")}
        cfgs = [c for c in CONFIGS if c["id"] in want]
    if args.smoke:
        global RESULTS, SUMMARY
        PANEL_CACHE = os.path.join(OUT_DIR_ROOT, "panel_smoke.npz")
        RESULTS = "/app/reports/overnight/selected_wave_smoke.jsonl"
        SUMMARY = "/app/reports/overnight/selected_wave_smoke_summary.json"
        cfgs = [dict(c) for c in cfgs[:3]]

    ml.set_exp_log("selected_wave")
    ml.log(f"selected_wave start KST={ml.now_kst().isoformat(timespec='seconds')} "
           f"limit={LIMIT} days={DAYS} seeds={SEEDS} smoke={args.smoke}")
    df, available = build_panel()
    ml.log(f"panel ready: rows={len(df)} features={len(available)}")

    # 서브셋 학습을 위해 curated 선택 함수를 pass-through 로 교체
    tc.select_curated_features = lambda names, allow_sentiment=False: list(names)

    results = []
    for cfg in cfgs:
        exp_id = cfg["id"]
        out_dir = os.path.join(OUT_DIR_ROOT, exp_id)
        os.makedirs(out_dir, exist_ok=True)
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        rec = {"exp": exp_id, "desc": cfg["desc"], "ts": ml.now_iso(), "status": "failed"}
        try:
            y = make_labels(df, cfg["label"], cfg["horizon"])
            split = ex._split_h(df, available, y)
            if split is None:
                raise RuntimeError("split failed")
            X_tr, X_va, X_te, y_tr, y_va, y_te, names, dates = split
            idx, sel_desc = subset_indices(names, cfg["select"], X_tr, y_tr)
            sel_names = [names[i] for i in idx]
            Xtr, Xva, Xte = X_tr[:, idx], X_va[:, idx], X_te[:, idx]
            ml.log(f"{exp_id} select={sel_desc} → {len(sel_names)} features | "
                   f"train={Xtr.shape} val={Xva.shape} test={Xte.shape}")

            seed_aucs = {}
            for seed in SEEDS:
                ens_auc, m_aucs, cur, ensemble = ml.train_seed(
                    Xtr, Xva, Xte, y_tr, y_va, y_te, sel_names,
                    out_dir, seed, 0.03, 4, 1500, True, None)
                seed_aucs[seed] = float(ens_auc)
                ml.log(f"  {exp_id} seed {seed}: test AUC={ens_auc:.4f}")
            vals = np.array(list(seed_aucs.values()))
            mean, std = float(vals.mean()), float(vals.std(ddof=1))
            rec.update({"status": "ok", "mean": mean, "std": std,
                        "seed_aucs": seed_aucs, "n_features": len(sel_names),
                        "select": sel_desc, "n_rows": int(len(y_te)),
                        "up_rate": float(np.mean(y_te))})
            ml.log(f"RESULT {exp_id} mean={mean:.4f} std={std:.4f} "
                   f"features={len(sel_names)} up_rate={np.mean(y_te):.3f}")
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            ml.log(f"FAIL {exp_id}: {e}")
            ml.log(traceback.format_exc(), raw=True)
        results.append(rec)
        with open(RESULTS, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ok = [r for r in results if r.get("status") == "ok"]
    best = max(ok, key=lambda r: r["mean"]) if ok else None
    summ = {"finished_at": ml.now_iso(), "best": best, "results": results}
    with open(SUMMARY, "w") as f:
        json.dump(summ, f, ensure_ascii=False, indent=2)
    ml.log(f"selected_wave done. best={best['exp'] if best else None} "
           f"mean={best['mean'] if best else None}")
    print(json.dumps({"best": {k: best[k] for k in ("exp", "mean", "std", "n_features")}
                      if best else None,
                      "all": [{"exp": r["exp"], "mean": r.get("mean"),
                               "std": r.get("std"), "status": r["status"]}
                              for r in results]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
