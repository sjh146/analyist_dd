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


def dedupe_names(names):
    """패널 피처명의 **중복 라벨을 제거**한다(두 번째부터 `__dupN` 접미사).

    ⚠ 왜 필수인가 (실측 2026-09-25, panel_420_asofpatch.npz):
      피처명 210개 중 14개가 중복(cross_trend, price_volume, target_ma_5, volume_price_trend…)이라
      `df[names]` 가 열을 **238개로 부풀리고 순서를 바꾼다**. 그래서
      `Xtr = transform_matrix(tr[base_names])` 의 열과 `base_names`(이름 목록)가 어긋나고,
      선별 인덱스 `Xtr[:, idx]` 가 **다른 열**을 학습에 넣는다 →
      '기록된 피처 이름'과 '실제 학습 열'이 달라져 **피처 단위 판정이 전부 무효**가 된다.
      증상은 조용하다(예외 없음, AUC 도 정상값). 그래서 이름을 1:1 로 만들어 뿌리에서 막는다.
    """
    seen, out = {}, []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}__dup{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def build_panel(cache, limit, days, log=print, **universe):
    """패널 캐시를 만들거나 재사용한다.

    universe: `tc._select_universe` 로 전달되는 확장 옵션(market/since/min_days/min_value/order).
    비우면 현행 기본값(KOSDAQ·코드순·최소 50일)이 그대로 쓰인다.
    ⚠ 캐시는 **파일명으로만** 구분된다 → 유니버스를 바꾸면 반드시 새 파일명을 써라
      (예: --panel /app/app/models/wf/panel_500.npz). 기존 패널을 덮으면 대조군이 사라진다.
    """
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        names = dedupe_names([str(n) for n in z["feature_names"]])
        Xc = z["X"]
        if Xc.shape[1] != len(names):
            raise RuntimeError(
                f"패널 파일 불일치: X 열 {Xc.shape[1]}개 vs 피처명 {len(names)}개 ({cache}) "
                f"— 이름↔열 매핑이 깨진 파일이다(중복 라벨로 저장된 흔적). 재빌드하라.")
        df = pd.DataFrame(Xc, columns=names)
        df["date"] = [str(d) for d in z["dates"]]
        df["stock_code"] = [str(c) for c in z["codes"]]
        df["price"] = z["price"].astype(float)
        log(f"panel cache 재사용: {df.shape} ({cache})")
        return df, names

    sig_at_start = ml.FeaturePipeline._feature_code_sig()
    pg = ml.connect_pg()
    try:
        codes = tc._select_universe(pg, limit, **universe)
        log(f"universe: {len(codes)} 종목 (limit={limit})")
        pipeline = ml.FeaturePipeline(pg_conn=pg)
        end = datetime.now()
        start = end - timedelta(days=days)
        log(f"빌드 구간: {start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}")
        df = pipeline.build_training_features(
            codes, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
            # 체크포인트: 부분 진척을 저장/재개한다. 컨테이너 재생성으로 docker exec 가
            # SIGKILL 되어도(실측 2026-09-25: 30,000/41,893 에서 전량 소실) 다음 실행이 이어받는다.
            checkpoint_path=cache,
            checkpoint_every=int(os.environ.get("PANEL_CK_EVERY", "500")))
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

    # ⚠ 빌드 중 피처 코드가 바뀌면 **저장하지 않는다**: 앞부분(옛 코드)과 뒷부분(새 코드) 행이
    # 섞이면 결측 패턴이 종목/기간과 상관돼 '종목 식별 증폭' 같은 유사누수가 생긴다(이 역할의
    # 실측 교훈). 리서처가 병행 편집 중일 때 9시간 빌드를 통째로 날리는 대신 명시적으로 실패시킨다.
    sig1 = ml.FeaturePipeline._feature_code_sig()
    if sig_at_start is not None and sig1 is not None and sig_at_start != sig1:
        for suf in (".rows.pkl", ".meta.json"):
            try:
                os.remove(cache + suf)      # 오염된 체크포인트는 버린다(깨끗한 재빌드 유도)
            except OSError:
                pass
        raise RuntimeError(
            f"빌드 중 피처 코드 변경 감지(code_sig {sig_at_start} → {sig1}) — 혼합 패널 방지를 위해 "
            f"저장하지 않음. 피처 작업이 멈춘 뒤 다시 실행하라.")

    os.makedirs(os.path.dirname(cache), exist_ok=True)
    np.savez_compressed(
        cache, X=df[available].values.astype(np.float32),
        feature_names=np.array(available),
        dates=df["date"].astype(str).values,
        codes=df["stock_code"].astype(str).values,
        price=df["price"].values.astype(np.float64))
    log(f"panel cache 저장: {cache}")
    # 성공했으면 체크포인트는 지운다(다음 유니버스 실행이 옛 진척을 물려받지 않도록).
    for suf in (".rows.pkl", ".meta.json"):
        try:
            os.remove(cache + suf)
        except OSError:
            pass
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
            # 라벨 참조일 기준 purge(실측 2026-09-25: 달력 h일 purge 는 갭 종목의 학습 라벨이
            # 테스트 구간 가격을 참조하는 행을 남긴다 — wf_label_sweep 에서 5폴드 22행 확인).
            d["_ref"] = df.groupby("stock_code", sort=False)["date"].shift(-cfg["horizon"])
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
                if "_ref" in tr.columns:
                    _bad = np.greater_equal(np.asarray(tr["_ref"].astype(str).values),
                                            np.asarray(dd[step * i]))
                    if _bad.any():
                        ml.log(f"  {exp_id} fold{i}: 라벨 참조일 purge {int(_bad.sum())}행 제거")
                        tr = tr[np.logical_not(_bad)]
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
