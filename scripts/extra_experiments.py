#!/usr/bin/env python3
"""2차 실험 웨이브 (데이터 위생·라벨 변형) — 목표 test AUC >= 0.60.

BC-250 레시피(curated43)를 그대로 두고, 데이터 위생/유니버스/라벨 변형만 바꿔
멀티시드 test AUC(평균±std)를 측정한다. 드라이버(`overnight_ml_loop.py`)의
헬퍼(훈련·아티팩트 저장·subprocess·게이트 승격 로직)를 import 해 재사용하고,
드라이버 파일은 수정하지 않는다. 산출물은 `_h` 접미사를 붙여 드라이버와 분리한다.

실험 (각 5시드 평균±std, test AUC 기준):
    H1 위생 필터   : curated43 + 학습/평가에서 거래량0·OHLC 동결 행 제외
    H2 유동성 유니버스 : 최근 20거래일 평균 거래대금 상위 300 KOSDAQ + curated43
    H3 시장상대 라벨   : 다음날 종가 수익률 > 그날 종목 중앙값 -> 1
    H4 위생+유동성 결합 : H1 ∩ H2
    H5 2일 호라이즌+위생: 라벨=2거래일 뒤 방향, H1 필터 적용
    H6 최근 가중       : 최근 60거래일 표본 가중 2.0

Usage (컨테이너 cwd /app):
    OMP_NUM_THREADS=2 python -u scripts/extra_experiments.py            # 전체
    OMP_NUM_THREADS=2 python -u scripts/extra_experiments.py --smoke    # 축소
    OMP_NUM_THREADS=2 python -u scripts/extra_experiments.py --diag     # DIAGNOSIS.md
    OMP_NUM_THREADS=2 python -u scripts/extra_experiments.py --only H1,H3
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")

_ML = None


def _load_driver():
    """overnight_ml_loop.py 를 lazy import (순수 함수 단위테스트 시 무거운 의존성 회피)."""
    global _ML
    if _ML is None:
        if "/app" not in sys.path:
            sys.path.insert(0, "/app")
        if "/app/scripts" not in sys.path:
            sys.path.insert(0, "/app/scripts")
        import overnight_ml_loop
        _ML = overnight_ml_loop
    return _ML


# --------------------------------------------------------------------------- #
# 순수 데이터 위생/라벨 함수 (DB·모델 비의존, 합성 데이터로 단위테스트 가능)
# --------------------------------------------------------------------------- #

def detect_zero_volume(df, volume_col="volume"):
    """거래량이 0(또는 <=0/결측)인 행 -> True 인 bool 배열 반환."""
    vol = pd.to_numeric(df[volume_col], errors="coerce").fillna(0.0)
    return (vol <= 0.0).values


def detect_frozen_ohlc(df, code_col="stock_code", date_col="trade_date",
                       ohlc_cols=("open_price", "high_price", "low_price", "close_price")):
    """직전 거래일과 OHLC 가 완전히 동일한(동결) 행 -> True 인 bool 배열 반환.

    같은 종목 내에서 날짜순으로 정렬한 뒤, 4개 OHLC 컬럼이 모두 이전 행과 같으면
    그 행을 '동결'(pykrx 보조 수집 특성의 중복 봉)으로 판정한다. 동결 구간의 첫 행은
    유지하고 뒤따르는 중복 행만 True 로 마킹한다.
    """
    df = df.sort_values([code_col, date_col]).reset_index(drop=True)
    same = np.ones(len(df), dtype=bool)
    for c in ohlc_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        prev = s.groupby(df[code_col], sort=False).shift(1)
        same &= (s == prev).fillna(False).values
    return same


def hygiene_filter(df, code_col="stock_code", date_col="trade_date",
                   volume_col="volume",
                   ohlc_cols=("open_price", "high_price", "low_price", "close_price")):
    """거래량0·OHLC 동결 행을 제외하고 (clean_df, stats) 반환."""
    df = df.sort_values([code_col, date_col]).reset_index(drop=True)
    vol0 = detect_zero_volume(df, volume_col)
    frozen = detect_frozen_ohlc(df, code_col, date_col, ohlc_cols)
    drop = vol0 | frozen
    stats = {
        "n_before": int(len(df)),
        "n_after": int((~drop).sum()),
        "n_zero_volume": int(vol0.sum()),
        "n_frozen": int(frozen.sum()),
        "n_dropped": int(drop.sum()),
    }
    return df[~drop].reset_index(drop=True), stats


def market_relative_labels(df, code_col="stock_code", date_col="date",
                           price_col="price", horizon=1):
    """다음 horizon 거래일 종가 수익률이 그날 전체 종목 중앙값보다 높으면 1.

    cross-sectional demean 라벨: 종목 간 순위만 쓰는 스크리너 의미상 자연스럽다.
    마지막 horizon 행(미래 수익률 없음)은 0 으로 처리(베이스 _create_labels 와 동일).
    """
    price = pd.to_numeric(df[price_col], errors="coerce")
    ret = df.groupby(code_col, sort=False)[price_col].transform(
        lambda s: s.shift(-horizon) / s - 1.0)
    med = ret.groupby(df[date_col]).transform("median")
    label = (ret > med).astype(float)
    return label.fillna(0.0).astype(int).values


def apply_recent_weight(X_train, y_train, train_dates, weight=2.0, recent_days=60):
    """최근 recent_days 거래일 표본을 weight 배만큼 복제(정수 반올림)해 가중을 준다."""
    weight = int(round(weight))
    if weight <= 1 or train_dates is None or len(train_dates) == 0:
        return X_train, y_train
    uniq = sorted(set(train_dates))
    recent = set(uniq[-recent_days:])
    mask = np.array([d in recent for d in train_dates], dtype=bool)
    if not mask.any():
        return X_train, y_train
    Xs = [X_train]
    ys = [y_train]
    for _ in range(weight - 1):
        Xs.append(X_train[mask])
        ys.append(y_train[mask])
    return np.concatenate(Xs, axis=0), np.concatenate(ys, axis=0)


# --------------------------------------------------------------------------- #
# 실험 정의
# --------------------------------------------------------------------------- #

APP = "/app"
MODELS_ROOT = os.path.join(APP, "app", "models")
CHAMPION_CAND_H = os.path.join(MODELS_ROOT, "champion_cand_h")
OVERNIGHT_DIR = os.path.join(APP, "reports", "overnight")
RESULTS_H = os.path.join(OVERNIGHT_DIR, "results_h.jsonl")
HB_H = os.path.join(OVERNIGHT_DIR, "heartbeat_h.txt")
FINAL_H = os.path.join(OVERNIGHT_DIR, "FINAL_REPORT_H.md")
DONE_H = os.path.join(OVERNIGHT_DIR, "done_h.flag")
DIAGNOSIS = os.path.join(OVERNIGHT_DIR, "DIAGNOSIS.md")
CAND_H_OOS = os.path.join(OVERNIGHT_DIR, "cand_h_oos.json")

GATE_MIN_AUC = 0.60
GATE_MAX_STD = 0.02
OOS_BEAT = 0.4924
DEFAULT_SEEDS = [0, 1, 2, 3, 4]

BASE_RECIPE = {
    "lr": 0.03, "depth": 4, "n_estimators": 1500,
    "allow_sentiment": False, "scale_pos_weight": None,
    "horizon": 1, "limit": 50, "days": 180,
}

EXPERIMENTS_H = [
    {"id": "H1", "kind": "hygiene", **BASE_RECIPE, "hygiene": True,
     "desc": "curated43 + 위생필터(거래량0/동결 제외)"},
    {"id": "H2", "kind": "liquidity", **BASE_RECIPE, "liquidity": True,
     "top_n": 300, "liq_days": 20,
     "desc": "유동성 상위 300 KOSDAQ + curated43"},
    {"id": "H3", "kind": "relative_labels", **BASE_RECIPE, "relative_labels": True,
     "desc": "curated43 + 시장상대(cross-sectional) 라벨"},
    {"id": "H4", "kind": "hygiene+liquidity", **BASE_RECIPE, "hygiene": True,
     "liquidity": True, "top_n": 300, "liq_days": 20,
     "desc": "유동성 상위 300 + 위생필터 + curated43"},
    {"id": "H5", "kind": "horizon2+hygiene", **BASE_RECIPE, "hygiene": True,
     "horizon": 2,
     "desc": "curated43 2일 호라이즌 + 위생필터"},
    {"id": "H6", "kind": "recent_weight", **BASE_RECIPE, "recent_weight": True,
     "recent_days": 60, "weight": 2.0,
     "desc": "curated43 + 최근 60거래일 가중 2.0"},
]

LIQUIDITY_SQL = """
WITH recent AS (
    SELECT stock_code, trade_date, trading_value,
           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY trade_date DESC) AS rn
    FROM market_data
    WHERE trade_date >= %s
),
top AS (
    SELECT stock_code, AVG(trading_value) AS avg_tv, COUNT(*) AS n
    FROM recent
    WHERE rn <= %s
    GROUP BY stock_code
    HAVING COUNT(*) >= %s
)
SELECT t.stock_code
FROM top t
JOIN stocks s ON t.stock_code = s.stock_code
WHERE s.market = 'KOSDAQ'
ORDER BY t.avg_tv DESC
LIMIT %s
"""


# --------------------------------------------------------------------------- #
# 하트비트 / 결과 기록 (드라이버와 동일 로직, `_h` 파일로 분리)
# --------------------------------------------------------------------------- #

HB_H_STATE = {"stage": "init", "exp": None, "detail": ""}
HB_H_LOCK = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_hb():
    ml = _load_driver()
    line = f"{_now_iso()} | {HB_H_STATE['stage']} | {HB_H_STATE['exp'] or '-'} | {HB_H_STATE['detail']}"
    with HB_H_LOCK:
        try:
            os.makedirs(OVERNIGHT_DIR, exist_ok=True)
            with open(HB_H, "w") as f:
                f.write(line + "\n")
        except OSError:
            pass
    print(f"[HB-H] {line}", flush=True)


def set_hb(stage, exp=None, detail=""):
    HB_H_STATE["stage"] = stage
    HB_H_STATE["exp"] = exp
    HB_H_STATE["detail"] = detail
    write_hb()


def _hb_loop_h():
    while True:
        time.sleep(120)
        write_hb()


def append_result_h(result):
    line = {
        "exp": result.get("exp"),
        "params": result.get("params", {}),
        "seed_aucs": [result["seed_aucs"].get(str(s)) for s in result.get("seeds", [])],
        "mean": result.get("mean"),
        "std": result.get("std"),
        "oos": result.get("oos"),
        "removed": result.get("removed"),
        "universe_n": result.get("universe_n"),
        "ts": result.get("ts"),
    }
    os.makedirs(OVERNIGHT_DIR, exist_ok=True)
    with open(RESULTS_H, "a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# 데이터 로딩 (위생/유동성/라벨 변형 적용)
# --------------------------------------------------------------------------- #

def _load_raw_market(pg, stock_codes, days):
    cur = pg.cursor()
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT stock_code, trade_date::text, open_price, high_price,
               low_price, close_price, volume
        FROM market_data
        WHERE stock_code = ANY(%s) AND trade_date >= %s
        ORDER BY stock_code, trade_date
    """, (list(stock_codes), start))
    rows = cur.fetchall()
    cur.close()
    cols = ["stock_code", "trade_date", "open_price", "high_price",
            "low_price", "close_price", "volume"]
    return pd.DataFrame(rows, columns=cols)


def select_liquidity_universe(pg, top_n=300, liq_days=20, min_days=10):
    start = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
    cur = pg.cursor()
    cur.execute(LIQUIDITY_SQL, (start, liq_days, min_days, top_n))
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    return codes


def _build_valid_pairs(pg, stock_codes, days):
    raw = _load_raw_market(pg, stock_codes, days)
    if raw.empty:
        return set(), {"n_before": 0, "n_after": 0, "n_zero_volume": 0,
                       "n_frozen": 0, "n_dropped": 0}
    clean, stats = hygiene_filter(raw)
    valid = set(zip(clean["stock_code"].astype(str), clean["trade_date"].astype(str)))
    return valid, stats


def _drop_invalid_dates(panel_df, valid_pairs):
    key = panel_df["stock_code"].astype(str) + "|" + panel_df["date"].astype(str)
    keep = key.isin(set(c + "|" + d for c, d in valid_pairs)).values
    return panel_df[keep].reset_index(drop=True), int((~keep).sum())


def _split_h(df, available, y, date_col="date"):
    """ml._split_dataset 와 동일한 분할 + 행별 날짜(문자열) 반환 (H6 가중용)."""
    X = df[available].values.astype(np.float32)
    X = np.nan_to_num(X, nan=0.0)
    y = np.asarray(y)
    valid = ~np.isnan(y.astype(np.float64))
    X = X[valid]
    y = y[valid]
    dates = None
    if date_col in df.columns:
        dates = df[date_col].astype(str).values[valid]
    if len(X) < 50:
        return None
    col_stds = np.std(X, axis=0)
    varying = col_stds > 0
    X = X[:, varying]
    available = [f for f, m in zip(available, varying) if m]
    n = len(X)
    te = int(n * 0.60)
    ve = int(n * 0.80)
    return (X[:te], X[te:ve], X[ve:], y[:te], y[te:ve], y[ve:], available, dates)


def load_h_dataset(pg, cfg):
    ml = _load_driver()
    if cfg.get("liquidity"):
        stock_codes = select_liquidity_universe(pg, cfg.get("top_n", 300),
                                                cfg.get("liq_days", 20))
    else:
        stock_codes = ml.tc._select_universe(pg, cfg.get("limit", 50))
    ml.log(f"universe: {len(stock_codes)} stocks (kind={cfg.get('kind')})")

    stats = None
    valid_pairs = None
    if cfg.get("hygiene"):
        valid_pairs, stats = _build_valid_pairs(pg, stock_codes, cfg["days"])
        ml.log(f"hygiene: vol0={stats['n_zero_volume']} frozen={stats['n_frozen']} "
               f"keep={stats['n_after']}/{stats['n_before']}")

    pipeline = ml.FeaturePipeline(pg_conn=pg)
    end = datetime.now()
    start = end - timedelta(days=cfg["days"])
    df = pipeline.build_training_features(
        stock_codes, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if df is None or len(df) < 100:
        return None

    if valid_pairs is not None:
        df, n_panel_dropped = _drop_invalid_dates(df, valid_pairs)
        stats = dict(stats)
        stats["n_panel_dropped"] = int(n_panel_dropped)
        ml.log(f"panel rows after hygiene drop: {len(df)} (dropped {n_panel_dropped})")

    base_features = pipeline.get_feature_names()
    df, available = ml._engineer_features(df, base_features)

    if cfg.get("relative_labels"):
        y = market_relative_labels(df, horizon=cfg.get("horizon", 1))
    else:
        y = ml._create_labels_horizon(df, cfg.get("horizon", 1))

    split = _split_h(df, available, y)
    if split is None:
        return None
    X_train, X_val, X_test, y_train, y_val, y_test, available, dates = split
    return {
        "X_train": X_train, "X_val": X_val, "X_test": X_test,
        "y_train": y_train, "y_val": y_val, "y_test": y_test,
        "feature_names": available, "stats": stats, "dates": dates,
        "n_universe": len(stock_codes),
    }


def run_h_experiment(cfg, seeds, out_dir):
    ml = _load_driver()
    pg = ml.connect_pg()
    try:
        data = load_h_dataset(pg, cfg)
        if data is None:
            raise RuntimeError("insufficient rows after transform")
        X_train = data["X_train"]
        X_val = data["X_val"]
        X_test = data["X_test"]
        y_train = data["y_train"]
        y_val = data["y_val"]
        y_test = data["y_test"]
        feature_names = data["feature_names"]
        stats = data.get("stats")
        dates = data.get("dates")

        if cfg.get("recent_weight"):
            train_dates = dates[:len(X_train)] if dates is not None else None
            X_train, y_train = apply_recent_weight(
                X_train, y_train, train_dates,
                weight=cfg.get("weight", 2.0), recent_days=cfg.get("recent_days", 60))
            ml.log(f"recent-weight: train={X_train.shape} (recent {cfg.get('recent_days')}d x{cfg.get('weight')})")

        ml.log(f"data: train={X_train.shape} val={X_val.shape} test={X_test.shape} "
               f"features={len(feature_names)}")
        curated = ml.tc.select_curated_features(feature_names, cfg["allow_sentiment"])
        ml.log(f"curated features: {len(curated)} (allow_sentiment={cfg['allow_sentiment']})")

        seed_aucs = {}
        model_aucs_sum = {}
        saved_ensemble = None
        for seed in seeds:
            set_hb("training", cfg["id"], f"seed {seed}/{seeds[-1]}")
            ens_auc, m_aucs, cur, ensemble = ml.train_seed(
                X_train, X_val, X_test, y_train, y_val, y_test, feature_names,
                out_dir, seed, cfg["lr"], cfg["depth"], cfg["n_estimators"],
                cfg["allow_sentiment"], cfg["scale_pos_weight"])
            seed_aucs[seed] = ens_auc
            for name, a in m_aucs.items():
                model_aucs_sum.setdefault(name, []).append(a)
            ml.log(f"seed {seed}: test AUC={ens_auc:.4f}")
            if saved_ensemble is None:
                saved_ensemble = ensemble

        vals = np.asarray([seed_aucs[s] for s in seeds], dtype=np.float64)
        mean = float(vals.mean())
        std = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
        model_aucs = {name: float(np.mean(v)) for name, v in model_aucs_sum.items()}

        n_rows = int(len(y_test))
        up_rate = float(y_test.mean())
        config = (f"kind={cfg['kind']} lr={cfg['lr']} depth={cfg['depth']} "
                  f"est={cfg['n_estimators']} horizon={cfg['horizon']} "
                  f"hygiene={cfg.get('hygiene', False)} "
                  f"liquidity={cfg.get('liquidity', False)} "
                  f"relative_labels={cfg.get('relative_labels', False)}")
        ml.save_artifacts(saved_ensemble, curated, out_dir, mean, std, model_aucs,
                          n_rows, up_rate, seeds, seed_aucs, config)
        return {"mean": mean, "std": std, "seed_aucs": {str(s): seed_aucs[s] for s in seeds},
                "model_aucs": model_aucs, "n_rows": n_rows, "n_features": len(curated),
                "up_rate": up_rate, "seeds": seeds, "removed": stats,
                "universe_n": data.get("n_universe")}
    finally:
        try:
            pg.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 게이트 / 백테스트 / 승격 (드라이버와 동일, `_h` 경로)
# --------------------------------------------------------------------------- #

def run_backtest_h():
    ml = _load_driver()
    cmd = [sys.executable, "-u", "scripts/phase_4_backtest.py",
           "--model-dir", "app/models/champion_cand_h",
           "--days", "90", "--out", CAND_H_OOS]
    set_hb("backtest", None, "phase_4_backtest on champion_cand_h")
    out, rc = ml.run_subprocess(cmd, timeout=3600)
    if rc != 0:
        ml.log(f"backtest exited rc={rc}")
    oos = None
    if os.path.exists(CAND_H_OOS):
        try:
            oos = float(json.load(open(CAND_H_OOS)).get("auc"))
        except Exception:
            oos = None
    m = re.search(r"Backtest AUC:\s*([\d.]+)", out)
    if oos is None and m:
        oos = float(m.group(1))
    return oos


def run_promote_h():
    ml = _load_driver()
    cmd = [sys.executable, "-m", "app.training.champion_promote",
           "--candidate", "app/models/champion_cand_h",
           "--champion", "app/models/champion",
           "--min-auc", "0.60", "--min-improvement", "0.0",
           "--summary-out", "app/reports/ml_result_h.json"]
    set_hb("promote", None, "champion_promote candidate_h -> champion")
    out, rc = ml.run_subprocess(cmd, timeout=600)
    try:
        start = out.index("{")
        end = out.rindex("}")
        return json.loads(out[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return {"promoted": False, "status": "unknown",
                "reason": "promote output parse failed", "tail": out[-2000:]}


def handle_gate_h(cfg, exp_out_dir, result):
    ml = _load_driver()
    ml.log(f"GATE PASSED for {cfg['id']}: mean={result['mean']:.4f} std={result['std']:.4f}")
    if os.path.exists(CHAMPION_CAND_H):
        shutil.rmtree(CHAMPION_CAND_H)
    shutil.copytree(exp_out_dir, CHAMPION_CAND_H)

    oos = run_backtest_h()
    result["oos"] = oos
    ml.log(f"candidate OOS AUC={oos} (incumbent ref {OOS_BEAT})")

    if oos is not None and oos > OOS_BEAT:
        pr = run_promote_h()
        result["promote"] = pr
        result["promoted"] = bool(pr.get("promoted"))
        result["backup_dir"] = pr.get("backup_dir")
        ml.log(f"promote result: status={pr.get('status')} promoted={pr.get('promoted')} "
               f"backup={pr.get('backup_dir')} reason={pr.get('reason')}")
    else:
        result["promoted"] = False
        result["promote"] = {"promoted": False,
                             "reason": f"OOS {oos} <= incumbent {OOS_BEAT}, keep champion"}
        ml.log(f"OOS not better than incumbent, promotion skipped (OOS={oos})")

    with open(os.path.join(OVERNIGHT_DIR, "gate_passed_h.flag"), "w") as f:
        f.write(f"{_now_iso()} {cfg['id']} mean={result['mean']:.4f} std={result['std']:.4f} "
                f"oos={oos} promoted={result.get('promoted', False)}\n")
    return True


# --------------------------------------------------------------------------- #
# 최종 보고
# --------------------------------------------------------------------------- #

def write_final_report_h(results, gate_passed, best, promote_info):
    lines = ["# 2차 실험 웨이브 (데이터 위생·라벨 변형) — 최종 보고", ""]
    lines.append(f"Generated: {_now_iso()} (UTC)")
    lines.append("")
    lines.append("## 실험 결과")
    lines.append("")
    lines.append("| Exp | 설명 | Seed AUCs | Mean | Std | OOS | 제외 행(위생) |")
    lines.append("|-----|------|-----------|------|-----|-----|---------------|")
    for r in results:
        seed_str = ", ".join(f"{s}:{r['seed_aucs'].get(str(s), '?')}" for s in r.get("seeds", []))
        mean = f"{r['mean']:.4f}" if r.get("mean") is not None else "N/A"
        std = f"{r['std']:.4f}" if r.get("std") is not None else "N/A"
        oos = f"{r['oos']:.4f}" if r.get("oos") is not None else "-"
        desc = r.get("params", {}).get("desc", "")
        removed = r.get("removed")
        if removed:
            rem = f"vol0={removed.get('n_zero_volume')}/frozen={removed.get('n_frozen')}"
        else:
            rem = "-"
        lines.append(f"| {r['exp']} | {desc} | {seed_str} | {mean} | {std} | {oos} | {rem} |")
    lines.append("")
    lines.append("## 최적 모델")
    if best:
        lines.append(f"- Experiment: {best['exp']}")
        lines.append(f"- Out dir: {best.get('out_dir', '-')}")
        lines.append(f"- Mean AUC: {best['mean']:.4f} ± {best['std']:.4f}")
        lines.append(f"- OOS AUC: {best.get('oos')}")
    else:
        lines.append("- 게이트 통과 실험 없음.")
    lines.append("")
    lines.append("## 게이트 판정")
    lines.append(f"- Gate passed: {gate_passed}")
    if promote_info:
        lines.append(f"- Promoted: {promote_info.get('promoted')}")
        lines.append(f"- Promote status: {promote_info.get('status')}")
        lines.append(f"- Reason: {promote_info.get('reason')}")
        lines.append(f"- Backup dir: {promote_info.get('backup_dir')}")
    lines.append("")
    lines.append("## 재현")
    lines.append("```")
    lines.append("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 python -u scripts/extra_experiments.py'")
    lines.append("```")
    lines.append("")
    lines.append("## 한계")
    lines.append("- 단일 CPU 상자, OMP/MKL 스레드 2개, 한 번에 실험 1개.")
    lines.append("- 게이트는 멀티시드 평균/std test AUC 기준.")
    lines.append("- H6 가중은 표본 복제(정수 가중)로 근사.")

    os.makedirs(OVERNIGHT_DIR, exist_ok=True)
    with open(FINAL_H, "w") as f:
        f.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# 진단 (--diag): DIAGNOSIS.md 즉시 생성
# --------------------------------------------------------------------------- #

def generate_diagnosis():
    ml = _load_driver()
    pg = ml.connect_pg()
    try:
        six_mo = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        cur = pg.cursor()
        cur.execute("""
            SELECT stock_code, trade_date::text, open_price, high_price,
                   low_price, close_price, volume
            FROM market_data WHERE trade_date >= %s
            ORDER BY stock_code, trade_date
        """, (six_mo,))
        rows = cur.fetchall()
        cur.close()
        raw = pd.DataFrame(rows, columns=["stock_code", "trade_date", "open_price",
                                          "high_price", "low_price", "close_price", "volume"])
        vol0 = detect_zero_volume(raw, "volume")
        frozen = detect_frozen_ohlc(raw)
        raw["_vol0"] = vol0
        raw["_frozen"] = frozen
        raw["_bad"] = vol0 | frozen
        per = raw.groupby("stock_code").agg(
            n=("stock_code", "size"),
            vol0=("_vol0", "sum"),
            frozen=("_frozen", "sum"),
            bad=("_bad", "sum"),
        ).reset_index()
        per["ratio"] = per["bad"] / per["n"]
        top20 = per.sort_values("ratio", ascending=False).head(20)

        # MARKET_DATA_VALID(O=H=L=0 제외)와 위생 필터의 중복도
        cur = pg.cursor()
        cur.execute("""
            WITH md AS (
              SELECT open_price, high_price, low_price, close_price, volume,
                     LAG(open_price) OVER (PARTITION BY stock_code ORDER BY trade_date) AS po,
                     LAG(high_price) OVER (PARTITION BY stock_code ORDER BY trade_date) AS ph,
                     LAG(low_price) OVER (PARTITION BY stock_code ORDER BY trade_date) AS pl,
                     LAG(close_price) OVER (PARTITION BY stock_code ORDER BY trade_date) AS pc
              FROM market_data WHERE trade_date >= %s
            )
            SELECT
              COUNT(*) FILTER (WHERE open_price=po AND high_price=ph AND low_price=pl AND close_price=pc) AS frozen,
              COUNT(*) FILTER (WHERE open_price=po AND high_price=ph AND low_price=pl AND close_price=pc
                               AND NOT (open_price=0 AND high_price=0 AND low_price=0)) AS frozen_nonzero,
              COUNT(*) FILTER (WHERE volume=0 OR volume IS NULL) AS vol0,
              COUNT(*) FILTER (WHERE (volume=0 OR volume IS NULL)
                               AND NOT (open_price=0 AND high_price=0 AND low_price=0)) AS vol0_nonzero
            FROM md
        """, (six_mo,))
        overlap = cur.fetchone()
        cur.close()

        curated50 = ml.tc._select_universe(pg, 50)
        liq300 = select_liquidity_universe(pg, 300, 20)
        raw_c50 = _load_raw_market(pg, curated50, 180)
        raw_l300 = _load_raw_market(pg, liq300, 180)
        _, st_c50 = hygiene_filter(raw_c50) if not raw_c50.empty else (None, {"n_before": 0, "n_after": 0, "n_zero_volume": 0, "n_frozen": 0, "n_dropped": 0})
        _, st_l300 = hygiene_filter(raw_l300) if not raw_l300.empty else (None, {"n_before": 0, "n_after": 0, "n_zero_volume": 0, "n_frozen": 0, "n_dropped": 0})

        lines = ["# 진단 요약 — 데이터 위생·라벨 변형 (2차 실험 웨이브)", ""]
        lines.append(f"Generated: {_now_iso()} (UTC)")
        lines.append("")
        lines.append("## (a) 동결/거래량0 비율 (최근 6개월, 종목별 상위 20)")
        lines.append("")
        lines.append("| rank | stock_code | rows | vol0 | frozen | bad | ratio |")
        lines.append("|------|-----------|------|------|--------|-----|-------|")
        for i, r in enumerate(top20.itertuples(), 1):
            lines.append(f"| {i} | {r.stock_code} | {int(r.n)} | {int(r.vol0)} | "
                         f"{int(r.frozen)} | {int(r.bad)} | {r.ratio:.4f} |")
        lines.append("")
        lines.append(f"- 전체(6개월): {int(vol0.sum())} 거래량0 / {int(frozen.sum())} 동결 / "
                     f"{int((vol0 | frozen).sum())} 중복제외 불량 / {len(raw)} 행 "
                     f"({100 * vol0.sum() / max(len(raw), 1):.2f}% / "
                     f"{100 * frozen.sum() / max(len(raw), 1):.2f}%)")
        lines.append("")
        lines.append("## (b) 실험별 제외 행 수 (위생 필터 기준)")
        lines.append("")
        lines.append(f"- curated50(180일): {st_c50['n_before']} 행 -> 위생 제외 {st_c50['n_dropped']} "
                     f"(vol0={st_c50['n_zero_volume']}, frozen={st_c50['n_frozen']})")
        lines.append(f"- liquidity300(180일): {st_l300['n_before']} 행 -> 위생 제외 {st_l300['n_dropped']} "
                     f"(vol0={st_l300['n_zero_volume']}, frozen={st_l300['n_frozen']})")
        lines.append("")
        lines.append("| Exp | 유니버스 | 라벨 | 위생 제외 행 |")
        lines.append("|-----|----------|------|--------------|")
        lines.append(f"| H1 | curated50 | 방향(1d) | {st_c50['n_dropped']} |")
        lines.append(f"| H2 | liquidity300 | 방향(1d) | 0 (유니버스 변경만) |")
        lines.append(f"| H3 | curated50 | 시장상대 | 0 (라벨 변형만) |")
        lines.append(f"| H4 | liquidity300 | 방향(1d) | {st_l300['n_dropped']} |")
        lines.append(f"| H5 | curated50 | 방향(2d) | {st_c50['n_dropped']} |")
        lines.append("")
        lines.append("## (c) val AUC vs test AUC 괴리 (E1 드라이버 로그 인용)")
        lines.append("")
        lines.append("E1 xgboost 학습 로그(`exp_E1.log`)에서 eval(val) AUC 는 0.66 내외인데 test AUC 는 0.51~0.53:")
        lines.append("")
        lines.append("```")
        lines.append("[375]  train's auc: 0.961999  eval's auc: 0.662184  -> test AUC=0.5146")
        lines.append("[282]  train's auc: 0.93591   eval's auc: 0.66288   -> test AUC=0.5066")
        lines.append("[268]  train's auc: 0.941207  eval's auc: 0.689599  -> test AUC=0.5188")
        lines.append("[310]  train's auc: 0.946814  eval's auc: 0.669937  -> test AUC=0.5205")
        lines.append("```")
        lines.append("")
        lines.append("중간 구간은 맞히지만 최근 구간(마지막 20%=test)에서 무너지는 전형적인 regime shift / 라벨 노이즈 패턴.")
        lines.append("")
        lines.append("## (d) H1 위생필터 vs 기존 MARKET_DATA_VALID 중복 (중요)")
        lines.append("")
        lines.append("파이프라인은 이미 `O=H=L=0`(거래정지/무거래) 행을 `MARKET_DATA_VALID` 로 제외한다.")
        lines.append("거래량0 행과 대부분의 동결 행이 여기에 해당해 H1 이 추가로 제거하는 행은 소수다:")
        lines.append("")
        lines.append(f"- 동결(OHLC 직전일 동일): {int(overlap[0])} 건 중 비영가(O=H=L≠0)는 **{int(overlap[1])}** 건뿐")
        lines.append(f"- 거래량0: {int(overlap[2])} 건 중 비영가는 **{int(overlap[3])}** 건 (전부 O=H=L=0)")
        lines.append("")
        lines.append("즉 거래량0 행은 이미 전부 제외되어 있고, H1 이 실제 패널에서 추가 제거하는 것은")
        lines.append("비영가 동결(가격 중복) 행 정도다. H1 의 기대 효과는 이 점에서 제한적일 수 있다.")

        os.makedirs(OVERNIGHT_DIR, exist_ok=True)
        with open(DIAGNOSIS, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"DIAGNOSIS written -> {DIAGNOSIS}")
    finally:
        try:
            pg.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 메인
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="2차 실험 웨이브 (데이터 위생·라벨 변형)")
    ap.add_argument("--smoke", action="store_true", help="축소 실행(문법·경로 검증)")
    ap.add_argument("--diag", action="store_true", help="DIAGNOSIS.md 만 생성하고 종료")
    ap.add_argument("--only", default=None, help="실험 id 쉼표 목록 (예: H1,H3)")
    args = ap.parse_args()

    ml = _load_driver()
    os.makedirs(OVERNIGHT_DIR, exist_ok=True)
    threading.Thread(target=_hb_loop_h, daemon=True).start()

    if args.diag:
        set_hb("diagnosis", detail="generating DIAGNOSIS.md")
        generate_diagnosis()
        set_hb("done", detail="diagnosis written")
        return 0

    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        experiments = [c for c in EXPERIMENTS_H if c["id"] in wanted]
    else:
        experiments = list(EXPERIMENTS_H)

    if args.smoke:
        for c in experiments:
            c["limit"] = min(c.get("limit", 50), 30)
            c["top_n"] = min(c.get("top_n", 300), 40)
            c["days"] = 60
            c["n_estimators"] = 40
            c["desc"] = c["desc"] + " [SMOKE]"
        seeds = [0, 1]
        gate_min_auc = 0.0
        gate_max_std = 1.0
    else:
        seeds = list(DEFAULT_SEEDS)
        gate_min_auc = GATE_MIN_AUC
        gate_max_std = GATE_MAX_STD

    deadline_new_exp = ml.next_kst_time(6, 30)
    deadline_final = ml.next_kst_time(6, 45)
    ml.log(f"start. KST now={ml.now_kst().isoformat(timespec='seconds')} "
           f"deadline_new_exp={deadline_new_exp.isoformat(timespec='seconds')} "
           f"deadline_final={deadline_final.isoformat(timespec='seconds')} "
           f"smoke={args.smoke} seeds={seeds}")

    set_hb("start", detail=f"smoke={args.smoke} seeds={seeds}")

    results = []
    gate_passed = False
    promote_info = None
    best = None

    for cfg in experiments:
        if not args.smoke and ml.now_kst() >= deadline_new_exp:
            ml.log("deadline 06:30 KST reached, no new experiments")
            break

        exp_id = cfg["id"]
        out_dir = os.path.join(MODELS_ROOT, "overnight_h", exp_id)
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)
        ml.set_exp_log(exp_id)
        set_hb("experiment_start", exp_id, cfg["desc"])
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")

        result = {"exp": exp_id, "params": {"desc": cfg["desc"], "kind": cfg.get("kind"),
                                            "lr": cfg["lr"], "depth": cfg["depth"],
                                            "n_estimators": cfg["n_estimators"],
                                            "limit": cfg["limit"], "days": cfg["days"],
                                            "horizon": cfg["horizon"],
                                            "hygiene": cfg.get("hygiene", False),
                                            "liquidity": cfg.get("liquidity", False),
                                            "relative_labels": cfg.get("relative_labels", False),
                                            "allow_sentiment": cfg["allow_sentiment"]},
                  "seed_aucs": {}, "mean": None, "std": None, "oos": None,
                  "removed": None, "universe_n": None,
                  "ts": _now_iso(), "status": "failed", "seeds": seeds}
        try:
            r = run_h_experiment(cfg, seeds, out_dir)
            result.update(r)
            result["status"] = "ok"
            result["out_dir"] = out_dir
            result["ts"] = _now_iso()
            result["mean"] = r["mean"]
            result["std"] = r["std"]
            result["oos"] = None
            ml.log(f"RESULT {exp_id} mean={r['mean']:.4f} std={r['std']:.4f} "
                   f"seed_aucs={result['seed_aucs']}")
        except Exception as e:
            result["error"] = str(e)
            result["ts"] = _now_iso()
            ml.log(f"FAIL {exp_id}: {e}")
            ml.log(traceback.format_exc(), raw=True)

        results.append(result)
        append_result_h(result)

        if result["status"] == "ok" and result["mean"] is not None:
            if result["mean"] >= gate_min_auc and result["std"] <= gate_max_std:
                if best is None or result["mean"] > best["mean"]:
                    best = result
                if not args.smoke:
                    gate_passed = handle_gate_h(cfg, out_dir, result)
                    promote_info = result.get("promote")
                    break

    if not gate_passed:
        for r in results:
            if r["status"] == "ok" and r["mean"] is not None:
                if best is None or r["mean"] > best["mean"]:
                    best = r

    write_final_report_h(results, gate_passed, best, promote_info)
    set_hb("done", detail=f"gate={gate_passed}")
    with open(DONE_H, "w") as f:
        f.write(f"{_now_iso()} done gate_passed={gate_passed}\n")
    ml.log(f"done. gate_passed={gate_passed} best={best['exp'] if best else None}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
