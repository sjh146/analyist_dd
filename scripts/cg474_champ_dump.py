#!/usr/bin/env python3
"""cg474_champ_dump — 배포 챔피언 예측을 CG473 과 **같은 패널·같은 폴드 테스트 행**에 덤프한다.

왜 (CG474, 2026-11-25)
  배포 챔피언의 돈 지표(순기대 -0.3431%p/세션·113세션·1771거래)와 CG473 덤프의 단순 core48
  arm(CO_smooth_d1_h5, k5 +0.582%p/세션)은 **프로토콜이 달라** 직접 비교가 무효다(창·청산·필터).
  같은 패널·같은 OOS 날짜·같은 청산(h5)·같은 체결필터로 나란히 재려면 챔피언 예측을
  **같은 테스트 행**에 덤프해야 한다.

충실도(배포 경로 재현)
  · 모델: /app/app/models/champion 의 xgboost/lightgbm/catboost 3종을 EnsembleModel 로 로드해
    **균등 평균**(ENSEMBLE_USE_STORED_WEIGHTS 미설정 = 배포 동작)으로 예측한다.
  · 피처: feature_names.json 의 순서 그대로. 패널에 없는 피처는 배포 추론과 동일하게 0.0
    (predictor.py: features.get(f, 0.0) → nan_to_num).
  · 라벨/폴드: wf_label_sweep 의 재료를 그대로 쓴다 — make_labels(kind=smooth, h=5, q=0.30),
    왼쪽 정렬 폴드(dd[step*i : step*(i+1)], purge 없음 — 폴드는 테스트 구간만 쓰므로
    학습 purge 는 이 덤프에 영향이 없다).

주의: 이 스크립트는 **측정 전용**이다. 모델·패널·champion/ 을 건드리지 않는다(쓰기는 --out 뿐).

실행(컨테이너):
  docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 python -u scripts/cg474_champ_dump.py'
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_wave as W  # noqa: E402

ml = W.ml


def fold_window_indices(n, n_folds, step, right_anchored=False):
    """wf_label_sweep.fold_window_indices 와 동일(왼쪽 정렬 경로만 사용)."""
    out = []
    for i in range(1, int(n_folds) + 1):
        if right_anchored:
            hi = int(n) - (int(n_folds) - i) * int(step)
            lo = hi - int(step)
            if lo < 1 or hi > int(n):
                raise RuntimeError(f"앵커 격자 범위 밖: fold{i} lo={lo} hi={hi} n={n} step={step}")
        else:
            lo = int(step) * i
            hi = min(int(n), int(step) * (i + 1))
        out.append((lo - 1, lo, hi))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/app/app/models/wf/panel_prod200_v6o.npz")
    ap.add_argument("--days", type=int, default=420)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--q", type=float, default=0.30)
    ap.add_argument("--models-dir", default="/app/app/models/champion")
    ap.add_argument("--tag", default="champ")
    ap.add_argument("--out", default="/app/reports/overnight/cg474_champ_preds.jsonl")
    ap.add_argument("--summary-out", default="/app/reports/overnight/cg474_champ_dump_summary.json")
    args = ap.parse_args(argv)

    from app.models.ensemble_model import EnsembleModel

    df, names = W.build_panel(args.panel, None, args.days, log=ml.log)
    df["_d"] = df["date"].astype(str)
    all_dates = sorted(df["_d"].unique())
    ml.log(f"panel rows={len(df)} dates={len(all_dates)} ({all_dates[0]} ~ {all_dates[-1]}) "
           f"codes={df['stock_code'].nunique()}")

    y = W.make_labels(df, "smooth", args.horizon, args.q)
    d = df.copy()
    d["_y"] = y
    d["_fwd"] = (df.groupby("stock_code", sort=False)["price"]
                 .transform(lambda s: s.shift(-args.horizon) / s - 1.0).values)
    d = d[~pd.isna(d["_y"])]
    dd = sorted(d["_d"].unique())
    n = len(dd)
    step = n // (args.folds + 1)
    ml.log(f"label rows={len(d)} dates={n} folds={args.folds} step={step}")

    with open(os.path.join(args.models_dir, "feature_names.json")) as f:
        feat = [str(x) for x in json.load(f)]

    present = [c for c in feat if c in df.columns]
    missing = [c for c in feat if c not in df.columns]
    ml.log(f"챔피언 피처 {len(feat)}개 중 패널 보유 {len(present)} · 결측(0 대체) {len(missing)}")
    if missing:
        ml.log("  결측 피처: " + ", ".join(missing[:20]) + (" ..." if len(missing) > 20 else ""))

    em = EnsembleModel(model_dir=os.path.dirname(args.models_dir))
    em.load(args.models_dir)
    ml.log(f"모델 로드: {em.model_names} · weights={em.val_weights or '균등(배포 기본)'}")

    Xdf = pd.DataFrame(index=df.index)
    for c in feat:
        Xdf[c] = (pd.to_numeric(df[c], errors="coerce").astype(np.float32)
                  if c in df.columns else np.float32(0.0))
    X_all = np.nan_to_num(Xdf.values.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)

    pos = {idx: i for i, idx in enumerate(df.index)}
    recs = []
    fold_stats = {}
    for i, (cut_idx, lo, hi) in enumerate(
            fold_window_indices(n, args.folds, step, right_anchored=False), start=1):
        cut, nxt = dd[cut_idx], dd[hi - 1]
        te = d[(d["_d"] > cut) & (d["_d"] <= nxt)]
        rows = np.array([pos[ix] for ix in te.index], dtype=int)
        p = np.asarray(em.predict(X_all[rows]), dtype=float).ravel()
        codes = te["stock_code"].astype(str).values
        ted = te["_d"].values
        yte = te["_y"].values.astype(int)
        fwd = np.asarray(te["_fwd"].values, dtype=float)
        for j in range(len(ted)):
            recs.append({"exp": args.tag, "fold": int(i), "date": str(ted[j]),
                         "code": str(codes[j]), "y_true": int(yte[j]),
                         "y_pred": float(p[j]), "fwd_ret": float(fwd[j])})
        auc = None
        try:
            from sklearn.metrics import roc_auc_score
            if len(set(yte.tolist())) > 1:
                auc = float(roc_auc_score(yte, p))
        except Exception as e:  # noqa: BLE001
            ml.log(f"  fold{i}: AUC 계산 실패({type(e).__name__}: {e})")
        fold_stats[f"fold{i}"] = {"cut": cut, "test_to": nxt, "test_rows": int(len(ted)),
                                 "auc": auc, "pred_mean": float(np.mean(p)) if len(p) else None}
        ml.log(f"  fold{i}: {cut}→{nxt} rows={len(ted)} auc={auc} pred_mean={np.mean(p):.4f}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")

    aucs = [v["auc"] for v in fold_stats.values() if v["auc"] is not None]
    summary = {
        "tag": args.tag,
        "panel": args.panel,
        "panel_rows": int(len(df)),
        "panel_dates": [all_dates[0], all_dates[-1]],
        "panel_codes": int(df["stock_code"].nunique()),
        "horizon": args.horizon, "q": args.q, "folds": args.folds,
        "label": "smooth(q=%.2f,h=%d)" % (args.q, args.horizon),
        "n_dump_rows": len(recs),
        "folds": fold_stats,
        "fold_auc_mean": float(np.mean(aucs)) if aucs else None,
        "fold_auc_std": float(np.std(aucs)) if aucs else None,
        "feat_total": len(feat), "feat_present": len(present), "feat_missing": missing,
        "model_names": list(em.model_names),
        "out": args.out,
    }
    with open(args.summary_out, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    ml.log(f"완료: {len(recs)}행 → {args.out} · fold AUC {summary['fold_auc_mean']}")
    print(json.dumps({k: summary[k] for k in ("n_dump_rows", "fold_auc_mean", "fold_auc_std",
                                              "feat_present", "feat_total")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
