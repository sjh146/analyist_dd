#!/usr/bin/env python3
"""Overnight autonomous ML experiment loop.

Runs a priority-ordered queue of curated-feature experiments sequentially (one at
a time), evaluates each by multi-seed mean/std test AUC, and stops early when a
candidate passes the gate (mean AUC >= 0.60 and std <= 0.02). A passing candidate
is written to app/models/champion_cand, OOS-backtested via phase_4_backtest.py,
and (if it beats the incumbent's OOS AUC) promoted via app.training.champion_promote.

Usage (inside stock_xgboost_ml, cwd /app):
    OMP_NUM_THREADS=2 python -u scripts/overnight_ml_loop.py
    OMP_NUM_THREADS=2 python -u scripts/overnight_ml_loop.py --smoke
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np

logging.getLogger("app.feature_engine.bayes_factor_features").setLevel(logging.ERROR)

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.training.trainer import Trainer
from app.models.ensemble_model import EnsembleModel

import train_curated as tc

KST = timezone(timedelta(hours=9))

APP = "/app"
MODELS_ROOT = os.path.join(APP, "app", "models")
CHAMPION_CAND = os.path.join(MODELS_ROOT, "champion_cand")
CHAMPION = os.path.join(MODELS_ROOT, "champion")
REPORTS_DIR = os.path.join(APP, "reports")
OVERNIGHT_DIR = os.path.join(REPORTS_DIR, "overnight")

GATE_MIN_AUC = 0.60
GATE_MAX_STD = 0.02
OOS_BEAT = 0.4924
DEFAULT_SEEDS = [0, 1, 2, 3, 4]
MAX_PANEL_ROWS = 30000

EXPERIMENTS = [
    {"id": "E1", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": False, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated43 lr=0.03 depth=4 est=1500 oversample on"},
    {"id": "E2", "lr": 0.02, "depth": 3, "n_estimators": 2000,
     "allow_sentiment": False, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated43 lr=0.02 depth=3 est=2000"},
    {"id": "E3", "lr": 0.05, "depth": 5, "n_estimators": 1000,
     "allow_sentiment": False, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated43 lr=0.05 depth=5 est=1000"},
    {"id": "E4", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": False, "scale_pos_weight": 1.0, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated43 lr=0.03 depth=4 est=1500 scale_pos_weight=1.0"},
    {"id": "E5", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 (sentiment/news on) lr=0.03 depth=4 est=1500"},
    {"id": "E6", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": False, "scale_pos_weight": None, "horizon": 1,
     "limit": 200, "days": 120, "panel": True,
     "desc": "173-feature canonical via panel cache lr=0.03 depth=4 est=1500"},
    {"id": "E7", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": False, "scale_pos_weight": None, "horizon": 2,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated43 2-day-horizon labels lr=0.03 depth=4 est=1500"},
]

EXP_LOGFILE = None
HB_STATE = {"stage": "init", "exp": None, "detail": ""}
HB_LOCK = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_kst():
    return datetime.now(KST)


def next_kst_time(hour, minute):
    n = now_kst()
    t = n.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if t <= n:
        t += timedelta(days=1)
    return t


def set_heartbeat(stage, exp=None, detail=""):
    HB_STATE["stage"] = stage
    HB_STATE["exp"] = exp
    HB_STATE["detail"] = detail
    write_heartbeat()


def write_heartbeat():
    line = f"{now_iso()} | {HB_STATE['stage']} | {HB_STATE['exp'] or '-'} | {HB_STATE['detail']}"
    with HB_LOCK:
        try:
            os.makedirs(OVERNIGHT_DIR, exist_ok=True)
            with open(os.path.join(OVERNIGHT_DIR, "heartbeat.txt"), "w") as f:
                f.write(line + "\n")
        except OSError:
            pass
    print(f"[HB] {line}", flush=True)


def _hb_loop():
    while True:
        time.sleep(120)
        write_heartbeat()


def log(msg, raw=False):
    line = msg if raw else f"{now_iso()} {msg}"
    print(line, flush=True)
    if EXP_LOGFILE:
        try:
            with open(EXP_LOGFILE, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass


def set_exp_log(exp_id):
    global EXP_LOGFILE
    os.makedirs(OVERNIGHT_DIR, exist_ok=True)
    EXP_LOGFILE = os.path.join(OVERNIGHT_DIR, f"exp_{exp_id}.log")


def append_result(result):
    line = {
        "exp": result.get("exp"),
        "params": result.get("params", {}),
        "seed_aucs": [result["seed_aucs"].get(str(s)) for s in result.get("seeds", [])],
        "mean": result.get("mean"),
        "std": result.get("std"),
        "oos": result.get("oos"),
        "ts": result.get("ts"),
    }
    os.makedirs(OVERNIGHT_DIR, exist_ok=True)
    with open(os.path.join(OVERNIGHT_DIR, "results.jsonl"), "a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def connect_pg():
    last = None
    for attempt in range(2):
        try:
            return tc._connect_pg()
        except Exception as e:
            last = e
            log(f"DB connect failed (attempt {attempt + 1}/2): {e}")
            if attempt == 0:
                set_heartbeat("db_retry_wait", detail=f"60s wait: {e}")
                time.sleep(60)
    raise RuntimeError(f"DB connect failed after retries: {last}")


def _safe_auc(y_true, probs):
    from sklearn.metrics import roc_auc_score
    try:
        return float(roc_auc_score(y_true, probs))
    except ValueError:
        return 0.5


def _create_labels_horizon(df, horizon=1):
    labels = np.zeros(len(df), dtype=int)
    if "stock_code" not in df.columns or "price" not in df.columns:
        return labels
    for code in df["stock_code"].unique():
        mask = df["stock_code"] == code
        idx = df[mask].index
        prices = df.loc[idx, "price"].values.astype(np.float64)
        n = len(prices)
        if n > horizon:
            next_up = prices[horizon:] > prices[:-horizon]
            vals = np.zeros(n, dtype=int)
            vals[:n - horizon] = next_up.astype(int)
            labels[idx] = vals
    return labels


def _engineer_features(df, base_features):
    df = df.copy()
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)
    elif "trade_date" in df.columns:
        df = df.sort_values("trade_date").reset_index(drop=True)

    available = [c for c in base_features if c in df.columns]
    added = []

    interaction_pairs = [
        ("return_1d", "volatility_20d", "momentum_vs_volatility"),
        ("return_5d", "return_20d", "trend_interaction"),
        ("volume_ratio_5", "return_5d", "volume_price_trend"),
        ("ma_position_5", "ma_position_20", "cross_trend"),
        ("volatility_20d", "volume_ratio_5", "volatility_volume"),
        ("return_1d", "return_5d", "short_medium_term_momentum"),
        ("return_5d", "ma_position_20", "trend_confirmation"),
        ("price", "volume_ratio_5", "price_volume"),
    ]
    for a, b, name in interaction_pairs:
        if a in df.columns and b in df.columns:
            df[name] = df[a] * df[b]
            added.append(name)

    rolling_stats = [
        ("return_5d", "return_5d_mean_10d", 10),
        ("volatility_20d", "volatility_20d_mean_10d", 10),
        ("volume_ratio_5", "volume_ratio_5_mean_10d", 10),
    ]
    if "stock_code" in df.columns:
        for col, new_name, window in rolling_stats:
            if col in df.columns:
                df[new_name] = df.groupby("stock_code")[col].transform(
                    lambda x: x.rolling(window=window, min_periods=1).mean())
                added.append(new_name)
    else:
        for col, new_name, window in rolling_stats:
            if col in df.columns:
                df[new_name] = df[col].rolling(window=window, min_periods=1).mean()
                added.append(new_name)

    rank_cols = ["return_5d", "return_20d", "volatility_20d",
                 "volume_ratio_5", "ma_position_5", "volume_ratio_20"]
    date_col = "date" if "date" in df.columns else ("trade_date" if "trade_date" in df.columns else None)
    if date_col is not None:
        for col in rank_cols:
            if col in df.columns:
                df[f"rank_{col}"] = df.groupby(date_col)[col].rank(pct=True)
                added.append(f"rank_{col}")

    target_ma_windows = [("return_1d", "target_ma_5", 5),
                         ("return_1d", "target_ma_10", 10),
                         ("return_1d", "target_ma_20", 20)]
    if "stock_code" in df.columns and "return_1d" in df.columns:
        for col, new_name, window in target_ma_windows:
            df[new_name] = df.groupby("stock_code")["return_1d"].transform(
                lambda x: x.rolling(window=window, min_periods=1).mean())
            added.append(new_name)

    available.extend(added)
    return df, available


def _split_dataset(df, available, y):
    X = df[available].values.astype(np.float32)
    X = np.nan_to_num(X, nan=0.0)
    valid = ~np.isnan(y.astype(np.float64))
    X = X[valid]
    y = y[valid]
    if len(X) < 50:
        return (None,) * 7
    col_stds = np.std(X, axis=0)
    varying = col_stds > 0
    X = X[:, varying]
    available = [f for f, m in zip(available, varying) if m]
    n = len(X)
    train_end = int(n * 0.60)
    val_end = int(n * 0.80)
    return (X[:train_end], X[train_end:val_end], X[val_end:],
            y[:train_end], y[train_end:val_end], y[val_end:], available)


def load_curated_dataset(pg, limit, days, horizon):
    stock_codes = tc._select_universe(pg, limit)
    log(f"universe: {len(stock_codes)} KOSDAQ stocks (limit={limit})")
    pipeline = FeaturePipeline(pg_conn=pg)
    trainer = Trainer(storage=None, feature_pipeline=pipeline)

    if horizon == 1:
        return trainer.prepare_training_data(stock_codes=stock_codes, days=days)

    end = datetime.now()
    start = end - timedelta(days=days)
    df = pipeline.build_training_features(
        stock_codes, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if df is None or len(df) < 100:
        return (None,) * 7
    base_features = pipeline.get_feature_names()
    df, available = _engineer_features(df, base_features)
    y = _create_labels_horizon(df, horizon=horizon)
    return _split_dataset(df, available, y)


def train_seed(X_train, X_val, X_test, y_train, y_val, y_test, feature_names,
               out_dir, seed, lr, depth, n_estimators, allow_sentiment, scale_pos_weight):
    curated = tc.select_curated_features(feature_names, allow_sentiment)
    if not curated:
        raise RuntimeError("no curated features selected")
    idx = [feature_names.index(f) for f in curated]
    X_train_c = X_train[:, idx]
    X_test_c = X_test[:, idx]

    rng = np.random.default_rng(seed)
    X_bal, y_bal = tc.oversample_balance(X_train_c, y_train, rng)
    Xc_t, yc_t, Xc_v, yc_v = tc.split_train_val(X_bal, y_bal, 0.67)

    ensemble = EnsembleModel(model_dir=out_dir)
    tc.apply_hyperparams(ensemble, lr, depth, n_estimators, seed)
    if scale_pos_weight is not None:
        for model in ensemble.models:
            p = getattr(model, "params", None)
            if p is not None and "scale_pos_weight" in p:
                p["scale_pos_weight"] = float(scale_pos_weight)
    ensemble.train(Xc_t, yc_t, Xc_v, yc_v)

    test_probs = ensemble.predict(X_test_c)
    ens_auc = _safe_auc(y_test, test_probs)

    model_aucs = {}
    for name, model in zip(ensemble.model_names, ensemble.models):
        try:
            model_aucs[name] = _safe_auc(y_test, model.predict(X_test_c))
        except Exception:
            model_aucs[name] = 0.5

    return ens_auc, model_aucs, curated, ensemble


def save_artifacts(ensemble, curated, out_dir, mean_auc, std_auc, model_aucs,
                   n_rows, up_rate, seeds, seed_aucs, config):
    os.makedirs(out_dir, exist_ok=True)
    ensemble.save(out_dir)
    ensemble.save_feature_names(curated, out_dir)
    with open(os.path.join(out_dir, "auc.txt"), "w") as f:
        f.write(f"{mean_auc:.6f}\n")
    meta = tc.build_result_dict(
        ensemble_auc=mean_auc, model_aucs=model_aucs, n_rows=n_rows,
        n_features=len(curated), up_rate=up_rate, seeds=seeds,
        seed_aucs={str(s): seed_aucs[s] for s in seeds},
        auc_mean=mean_auc, auc_std=std_auc, config=config)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    meta_path = os.path.join(out_dir, f"training-result-{ts}.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta, meta_path


def run_curated_experiment(cfg, seeds, out_dir):
    pg = connect_pg()
    try:
        data = load_curated_dataset(pg, cfg["limit"], cfg["days"], cfg["horizon"])
        X_train, X_val, X_test, y_train, y_val, y_test, feature_names = data
        if X_train is None or len(X_train) < 50:
            raise RuntimeError("prepare_training_data failed / insufficient rows")
        log(f"data: train={X_train.shape} val={X_val.shape} test={X_test.shape} features={len(feature_names)}")

        curated = tc.select_curated_features(feature_names, cfg["allow_sentiment"])
        log(f"curated features: {len(curated)} (allow_sentiment={cfg['allow_sentiment']})")

        seed_aucs = {}
        model_aucs_sum = {}
        saved_ensemble = None
        for seed in seeds:
            set_heartbeat("training", cfg["id"], f"seed {seed}/{seeds[-1]}")
            ens_auc, m_aucs, cur, ensemble = train_seed(
                X_train, X_val, X_test, y_train, y_val, y_test, feature_names,
                out_dir, seed, cfg["lr"], cfg["depth"], cfg["n_estimators"],
                cfg["allow_sentiment"], cfg["scale_pos_weight"])
            seed_aucs[seed] = ens_auc
            for name, a in m_aucs.items():
                model_aucs_sum.setdefault(name, []).append(a)
            log(f"seed {seed}: test AUC={ens_auc:.4f}")
            if saved_ensemble is None:
                saved_ensemble = ensemble

        vals = np.asarray([seed_aucs[s] for s in seeds], dtype=np.float64)
        mean = float(vals.mean())
        std = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
        model_aucs = {name: float(np.mean(v)) for name, v in model_aucs_sum.items()}

        n_rows = int(len(y_test))
        up_rate = float(y_test.mean())
        config = (f"curated lr={cfg['lr']} depth={cfg['depth']} est={cfg['n_estimators']} "
                  f"sentiment={'on' if cfg['allow_sentiment'] else 'off'} "
                  f"spw={cfg['scale_pos_weight']} horizon={cfg['horizon']} "
                  f"days={cfg['days']} limit={cfg['limit']}")
        save_artifacts(saved_ensemble, curated, out_dir, mean, std, model_aucs,
                       n_rows, up_rate, seeds, seed_aucs, config)
        return {"mean": mean, "std": std, "seed_aucs": {str(s): seed_aucs[s] for s in seeds},
                "model_aucs": model_aucs, "n_rows": n_rows, "n_features": len(curated),
                "up_rate": up_rate, "seeds": seeds}
    finally:
        try:
            pg.close()
        except Exception:
            pass


def run_subprocess(cmd, cwd="/app", timeout=None, extra_env=None):
    log(f"run: {' '.join(cmd)}")
    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", "2")
    env.setdefault("MKL_NUM_THREADS", "2")
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    log(out, raw=True)
    return out, p.returncode


def run_panel_experiment(cfg, seeds, out_dir, panel_limit):
    cache_dir = os.path.join(MODELS_ROOT, "exp_panel")
    build_cmd = [sys.executable, "-u", "scripts/ml_auc_experiment.py", "build",
                 "--stock-limit", str(panel_limit), "--days", str(cfg["days"]),
                 "--cache-dir", cache_dir]
    set_heartbeat("panel_build", cfg["id"], f"stock_limit={panel_limit} days={cfg['days']}")
    out, rc = run_subprocess(build_cmd, timeout=3600)
    if rc != 0:
        raise RuntimeError(f"panel build failed rc={rc}")

    ms_cmd = [sys.executable, "-u", "scripts/ml_auc_experiment.py", "multiseed",
              "--cache-dir", cache_dir,
              "--seeds", ",".join(str(s) for s in seeds),
              "--n-estimators", str(cfg["n_estimators"]),
              "--max-depth", str(cfg["depth"]),
              "--learning-rate", str(cfg["lr"]),
              "--save-dir", out_dir]
    set_heartbeat("training", cfg["id"], "multiseed via panel cache")
    out, rc = run_subprocess(ms_cmd, timeout=7200)
    if rc != 0:
        raise RuntimeError(f"multiseed failed rc={rc}")

    seed_ens = {}
    per_model = {"xgboost": [], "lightgbm": [], "catboost": []}
    for line in out.splitlines():
        m = re.search(r"seed\s+(\d+):\s+xgb=([\d.]+)\s+lgb=([\d.]+)\s+cat=([\d.]+)\s+ens=([\d.]+)", line)
        if m:
            seed_ens[int(m.group(1))] = float(m.group(5))
            per_model["xgboost"].append(float(m.group(2)))
            per_model["lightgbm"].append(float(m.group(3)))
            per_model["catboost"].append(float(m.group(4)))
    if not seed_ens:
        raise RuntimeError("could not parse multiseed results")

    vals = np.asarray([seed_ens[s] for s in seeds], dtype=np.float64)
    mean = float(vals.mean())
    std = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
    model_aucs = {k: (float(np.mean(v)) if v else 0.5) for k, v in per_model.items()}

    meta_path = os.path.join(cache_dir, "meta.json")
    n_rows = 0
    if os.path.exists(meta_path):
        try:
            n_rows = int(json.load(open(meta_path)).get("n_rows", 0))
        except Exception:
            n_rows = 0

    if mean >= GATE_MIN_AUC:
        rewrite_panel_contract(out_dir, mean, std, model_aucs, n_rows, seeds, seed_ens)

    return {"mean": mean, "std": std, "seed_aucs": {str(s): seed_ens[s] for s in seeds},
            "model_aucs": model_aucs, "n_rows": n_rows, "n_features": 173,
            "up_rate": None, "seeds": seeds}


def rewrite_panel_contract(out_dir, mean, std, model_aucs, n_rows, seeds, seed_ens):
    with open(os.path.join(out_dir, "auc.txt"), "w") as f:
        f.write(f"{mean:.6f}\n")
    meta = {
        "retrained_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": int(n_rows),
        "n_features": 173,
        "model_aucs": {k: round(v, 4) for k, v in model_aucs.items()},
        "ensemble_auc": round(mean, 4),
        "val_frac": 0.2,
        "seeds": [int(s) for s in seeds],
        "seed_aucs": {str(s): round(seed_ens[s], 6) for s in seeds},
        "auc_mean": round(mean, 6),
        "auc_std": round(std, 6),
        "up_rate": None,
        "config": "panel canonical 173-feature multiseed",
    }
    with open(os.path.join(out_dir, f"training-result-{datetime.now():%Y%m%d-%H%M%S}.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def run_backtest():
    out_path = os.path.join(OVERNIGHT_DIR, "cand_oos.json")
    cmd = [sys.executable, "-u", "scripts/phase_4_backtest.py",
           "--model-dir", "app/models/champion_cand",
           "--days", "90", "--out", out_path]
    set_heartbeat("backtest", None, "phase_4_backtest on champion_cand")
    out, rc = run_subprocess(cmd, timeout=3600)
    if rc != 0:
        log(f"backtest exited rc={rc}")
    oos = None
    if os.path.exists(out_path):
        try:
            oos = float(json.load(open(out_path)).get("auc"))
        except Exception:
            oos = None
    m = re.search(r"Backtest AUC:\s*([\d.]+)", out)
    if oos is None and m:
        oos = float(m.group(1))
    return oos


def run_promote():
    cmd = [sys.executable, "-m", "app.training.champion_promote",
           "--candidate", "app/models/champion_cand",
           "--champion", "app/models/champion",
           "--min-auc", "0.60", "--min-improvement", "0.0",
           "--summary-out", "app/reports/ml_result.json"]
    set_heartbeat("promote", None, "champion_promote candidate -> champion")
    out, rc = run_subprocess(cmd, timeout=600)
    try:
        start = out.index("{")
        end = out.rindex("}")
        return json.loads(out[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return {"promoted": False, "status": "unknown", "reason": "promote output parse failed",
                "tail": out[-2000:]}


def wait_for_inputs():
    required = [
        os.path.join(APP, "scripts", "train_curated.py"),
        os.path.join(APP, "scripts", "ml_auc_experiment.py"),
        os.path.join(APP, "scripts", "phase_4_backtest.py"),
    ]
    deadline = datetime.now() + timedelta(minutes=45)
    while datetime.now() < deadline:
        missing = [p for p in required if not os.path.exists(p)]
        if not missing:
            return
        log(f"waiting for inputs, missing: {[os.path.basename(p) for p in missing]}")
        set_heartbeat("waiting_inputs", detail=f"missing {len(missing)} file(s)")
        time.sleep(30)
    missing = [p for p in required if not os.path.exists(p)]
    log(f"inputs still missing after 45min: {missing}")


def handle_gate(cfg, exp_out_dir, result):
    log(f"GATE PASSED for {cfg['id']}: mean={result['mean']:.4f} std={result['std']:.4f}")
    if os.path.exists(CHAMPION_CAND):
        shutil.rmtree(CHAMPION_CAND)
    shutil.copytree(exp_out_dir, CHAMPION_CAND)

    oos = run_backtest()
    result["oos"] = oos
    log(f"candidate OOS AUC={oos} (incumbent ref {OOS_BEAT})")

    if oos is not None and oos > OOS_BEAT:
        pr = run_promote()
        result["promote"] = pr
        result["promoted"] = bool(pr.get("promoted"))
        result["backup_dir"] = pr.get("backup_dir")
        log(f"promote result: status={pr.get('status')} promoted={pr.get('promoted')} "
            f"backup={pr.get('backup_dir')} reason={pr.get('reason')}")
    else:
        result["promoted"] = False
        result["promote"] = {"promoted": False,
                             "reason": f"OOS {oos} <= incumbent {OOS_BEAT}, keep champion"}
        log(f"OOS not better than incumbent, promotion skipped (OOS={oos})")

    with open(os.path.join(OVERNIGHT_DIR, "gate_passed.flag"), "w") as f:
        f.write(f"{now_iso()} {cfg['id']} mean={result['mean']:.4f} std={result['std']:.4f} "
                f"oos={oos} promoted={result.get('promoted', False)}\n")
    return True


def write_final_report(results, gate_passed, best, promote_info):
    lines = ["# Overnight ML Experiment Loop — Final Report", ""]
    lines.append(f"Generated: {now_iso()} (UTC)")
    lines.append("")
    lines.append("## Experiment results")
    lines.append("")
    lines.append("| Exp | Config | Seed AUCs | Mean | Std | OOS |")
    lines.append("|-----|--------|-----------|------|-----|-----|")
    for r in results:
        seed_str = ", ".join(f"{s}:{r['seed_aucs'].get(str(s), '?')}" for s in r.get("seeds", []))
        mean = f"{r['mean']:.4f}" if r.get("mean") is not None else "N/A"
        std = f"{r['std']:.4f}" if r.get("std") is not None else "N/A"
        oos = f"{r['oos']:.4f}" if r.get("oos") is not None else "-"
        desc = r.get("params", {}).get("desc", "")
        lines.append(f"| {r['exp']} | {desc} | {seed_str} | {mean} | {std} | {oos} |")
    lines.append("")
    lines.append("## Best model")
    if best:
        lines.append(f"- Experiment: {best['exp']}")
        lines.append(f"- Out dir: {best.get('out_dir', '-')}")
        lines.append(f"- Mean AUC: {best['mean']:.4f} ± {best['std']:.4f}")
        lines.append(f"- OOS AUC: {best.get('oos')}")
    else:
        lines.append("- No experiment passed the gate.")
    lines.append("")
    lines.append("## Gate verdict")
    lines.append(f"- Gate passed: {gate_passed}")
    if promote_info:
        lines.append(f"- Promoted: {promote_info.get('promoted')}")
        lines.append(f"- Promote status: {promote_info.get('status')}")
        lines.append(f"- Reason: {promote_info.get('reason')}")
        lines.append(f"- Backup dir: {promote_info.get('backup_dir')}")
    lines.append("")
    lines.append("## Reproduction")
    lines.append("```")
    lines.append("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 python -u scripts/overnight_ml_loop.py'")
    lines.append("```")
    lines.append("")
    lines.append("## Rollback")
    lines.append("```")
    if promote_info and promote_info.get("backup_dir"):
        lines.append(f"# restore previous champion backup (last kept in app/models/):")
        lines.append(f"rm -rf app/models/champion && cp -r {promote_info.get('backup_dir')} app/models/champion")
    else:
        lines.append("# no promotion performed; champion unchanged")
    lines.append("```")
    lines.append("")
    lines.append("## Limitations")
    lines.append("- Single CPU box (7.9GB), OMP/MKL threads capped at 2; one experiment at a time.")
    lines.append("- Gate is on multi-seed mean/std test AUC; single-seed maxima ignored.")
    lines.append("- E6 (173-feature panel) and E7 (2-day horizon) are best-effort secondary configs.")
    lines.append("- Backtest OOS uses a stratified 30/20 KOSPI/KOSDAQ universe (seed 42).")

    os.makedirs(OVERNIGHT_DIR, exist_ok=True)
    with open(os.path.join(OVERNIGHT_DIR, "FINAL_REPORT.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Overnight ML experiment loop")
    ap.add_argument("--smoke", action="store_true",
                    help="reduced single-experiment run to validate the driver")
    args = ap.parse_args()

    os.makedirs(OVERNIGHT_DIR, exist_ok=True)
    threading.Thread(target=_hb_loop, daemon=True).start()

    if args.smoke:
        experiments = [dict(EXPERIMENTS[0])]
        experiments[0]["limit"] = 30
        experiments[0]["days"] = 60
        experiments[0]["n_estimators"] = 60
        experiments[0]["desc"] = experiments[0]["desc"] + " [SMOKE limit=30 days=60 est=60]"
        seeds = [0, 1]
        gate_min_auc = 0.0
        gate_max_std = 1.0
    else:
        experiments = list(EXPERIMENTS)
        seeds = list(DEFAULT_SEEDS)
        gate_min_auc = GATE_MIN_AUC
        gate_max_std = GATE_MAX_STD

    deadline_new_exp = next_kst_time(6, 15)
    deadline_final = next_kst_time(6, 45)
    log(f"start. KST now={now_kst().isoformat(timespec='seconds')} "
        f"deadline_new_exp={deadline_new_exp.isoformat(timespec='seconds')} "
        f"deadline_final={deadline_final.isoformat(timespec='seconds')} "
        f"smoke={args.smoke} seeds={seeds}")

    set_heartbeat("start", detail=f"smoke={args.smoke} seeds={seeds}")
    wait_for_inputs()

    results = []
    gate_passed = False
    promote_info = None
    best = None
    panel_limit = 200

    for cfg in experiments:
        if not args.smoke and now_kst() >= deadline_new_exp:
            log("deadline 06:15 KST reached, no new experiments")
            break

        exp_id = cfg["id"]
        out_dir = os.path.join(MODELS_ROOT, "overnight", exp_id)
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)
        set_exp_log(exp_id)
        set_heartbeat("experiment_start", exp_id, cfg["desc"])
        log(f"=== {exp_id}: {cfg['desc']} ===")

        result = {"exp": exp_id, "params": {"desc": cfg["desc"], "lr": cfg["lr"],
                                            "depth": cfg["depth"], "n_estimators": cfg["n_estimators"],
                                            "limit": cfg["limit"], "days": cfg["days"],
                                            "horizon": cfg["horizon"], "panel": cfg["panel"],
                                            "scale_pos_weight": cfg["scale_pos_weight"],
                                            "allow_sentiment": cfg["allow_sentiment"]},
                  "seed_aucs": {}, "mean": None, "std": None, "oos": None,
                  "ts": now_iso(), "status": "failed", "seeds": seeds}
        try:
            if cfg["panel"]:
                r = run_panel_experiment(cfg, seeds, out_dir, panel_limit)
            else:
                r = run_curated_experiment(cfg, seeds, out_dir)
            result.update(r)
            result["status"] = "ok"
            result["out_dir"] = out_dir
            result["ts"] = now_iso()
            result["mean"] = r["mean"]
            result["std"] = r["std"]
            result["oos"] = None
            log(f"RESULT {exp_id} mean={r['mean']:.4f} std={r['std']:.4f} "
                f"seed_aucs={result['seed_aucs']}")
        except Exception as e:
            result["error"] = str(e)
            result["ts"] = now_iso()
            log(f"FAIL {exp_id}: {e}")
            log(traceback.format_exc(), raw=True)
            err = str(e).lower()
            if "memory" in err or "killed" in err or "cannot allocate" in err or "oom" in err:
                panel_limit = max(100, panel_limit // 2)
                log(f"resource signal detected -> reducing panel_limit to {panel_limit}")

        results.append(result)
        append_result(result)

        if result["status"] == "ok" and result["mean"] is not None:
            if result["mean"] >= gate_min_auc and result["std"] <= gate_max_std:
                if best is None or result["mean"] > best["mean"]:
                    best = result
                if not args.smoke:
                    gate_passed = handle_gate(cfg, out_dir, result)
                    promote_info = result.get("promote")
                    break

    if not gate_passed:
        for r in results:
            if r["status"] == "ok" and r["mean"] is not None:
                if best is None or r["mean"] > best["mean"]:
                    best = r

    write_final_report(results, gate_passed, best, promote_info)
    set_heartbeat("done", detail=f"gate={gate_passed}")
    with open(os.path.join(OVERNIGHT_DIR, "done.flag"), "w") as f:
        f.write(f"{now_iso()} done gate_passed={gate_passed}\n")
    log(f"done. gate_passed={gate_passed} best={best['exp'] if best else None}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
