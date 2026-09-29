"""Deterministic champion retrain with an explicit train/inference feature contract.

WHY: the previous champion was trained by the strategy-experiment loop, which
trained each model on a DIFFERENT feature subset without persisting the column
order (catboost=23, lightgbm=62 positional columns, no names) while
feature_names.json was a separately sorted list. Inference fed features in json
order -> columns misaligned + ~20 features always 0.0 -> the ensemble collapsed
to a near-constant all-DOWN prediction (up:0/down:1668, hit Aug 2026).

FIX: train ALL three models on the SAME canonical feature matrix whose column
order is ``FeaturePipeline.get_feature_names()`` (a fixed sorted list) and write
``feature_names.json`` in that EXACT order. The screener builds its vector from
that same json with ``features.get(f, 0.0)``, so training and inference now use
identical positions. CatBoost additionally receives the real feature names so
its model metadata is debuggable.

Usage (in the xgboost-ml container):
    docker compose run --rm xgboost-ml python -m app.training.retrain_champion \
        --days 120 --stock-limit 200
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.catboost_model import CatBoostModel
from app.models.lightgbm_model import LightGBMModel
from app.models.xgboost_model import XGBoostModel

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = "app/models/champion"


def _create_labels(df: pd.DataFrame) -> np.ndarray:
    """1-day forward close direction per stock (mirrors Trainer._create_labels)."""
    labels = np.zeros(len(df), dtype=int)
    if "stock_code" not in df.columns or "price" not in df.columns:
        return labels
    for code in df["stock_code"].unique():
        mask = df["stock_code"] == code
        idx = df[mask].index
        prices = df.loc[idx, "price"].values.astype(np.float64)
        if len(prices) >= 2:
            next_up = prices[1:] > prices[:-1]
            vals = np.zeros(len(prices), dtype=int)
            vals[:-1] = next_up.astype(int)
            labels[idx] = vals
    return labels


def _add_cross_sectional_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Batch-level cross-sectional ranks, replicating
    FeaturePipeline.compute_cross_sectional_ranks / Trainer (trainer.py:107-118)."""
    rank_cols = [
        "return_5d", "return_20d", "volatility_20d",
        "volume_ratio_5", "ma_position_5", "volume_ratio_20",
    ]
    date_col = "date" if "date" in df.columns else ("trade_date" if "trade_date" in df.columns else None)
    out = df.copy()
    if date_col is None:
        return out
    for col in rank_cols:
        if col in out.columns:
            out[f"rank_{col}"] = out.groupby(date_col)[col].rank(pct=True)
    return out


def _pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _select_stocks(pg, limit: int) -> List[str]:
    """재학습 유니버스 — ETF/ETN 제외 + 최근 데이터 우선 (universe.py 참조)."""
    from app.training.universe import select_training_universe

    return select_training_universe(pg, limit=limit, min_days=30, seed=0)


def retrain_champion(
    df: pd.DataFrame,
    out_dir: str,
    val_frac: float = 0.2,
    n_estimators: int = 500,
    seed: int = 0,
    data_start: Optional[str] = None,
    data_end: Optional[str] = None,
) -> dict:
    """Core retrain (DB-free, testable): train 3 models on ONE canonical matrix.

    Returns a summary dict with per-model val AUC, ensemble AUC, feature count.
    """
    from app.feature_engine.feature_pipeline import FeaturePipeline as _FP

    os.makedirs(out_dir, exist_ok=True)

    df = _add_cross_sectional_ranks(df)
    df = df.sort_values("date").reset_index(drop=True)
    y = _create_labels(df)

    # Canonical contract: the FULL sorted feature list, same order the screener
    # builds. Missing columns are 0-filled on BOTH sides (train + inference).
    canonical: List[str] = _FP().get_feature_names()
    X = np.zeros((len(df), len(canonical)), dtype=np.float32)
    for j, name in enumerate(canonical):
        if name in df.columns:
            col = df[name].to_numpy(dtype=np.float64)
            X[:, j] = np.nan_to_num(col, nan=0.0, posinf=0.0, neginf=0.0)

    # Chronological split (walk-forward style: train on past, val on future).
    split = int(len(df) * (1.0 - val_frac))
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    up_rate = float(y.mean())

    models = [
        ("xgboost", XGBoostModel(n_estimators=n_estimators, random_state=seed)),
        ("lightgbm", LightGBMModel(n_estimators=n_estimators, random_state=seed)),
        ("catboost", CatBoostModel(iterations=n_estimators, random_state=seed)),
    ]
    weights: dict = {}
    aucs: dict = {}
    saved: List[str] = []
    for name, model in models:
        try:
            metrics = model.train(X_train, y_train, X_val, y_val)
            auc = 0.5
            if X_val is not None and len(X_val) > 10:
                try:
                    auc = roc_auc_score(y_val, model.predict(X_val))
                except Exception:
                    auc = 0.5
            aucs[name] = round(float(auc), 4)
            weights[name] = max(auc - 0.5, 0.01)
            path = os.path.join(out_dir, f"{name}_model.pkl")
            model.save(path)
            saved.append(path)
            logger.info("%s val AUC=%.4f -> saved %s", name, auc, path)
        except Exception as e:
            logger.warning("%s training failed: %s", name, e)
            aucs[name] = None

    if not saved:
        raise RuntimeError("no model trained")

    # Ensemble AUC (weighted soft-vote on val).
    ens_auc = 0.5
    if X_val is not None and len(X_val) > 10:
        probs = np.zeros(len(X_val))
        tw = 0.0
        for name, model in models:
            w = weights.get(name)
            if w is None:
                continue
            probs += w * model.predict(X_val)
            tw += w
        if tw > 0:
            try:
                ens_auc = roc_auc_score(y_val, probs / tw)
            except Exception:
                ens_auc = 0.5

    # Persist the contract + metadata.
    with open(os.path.join(out_dir, "feature_names.json"), "w") as f:
        json.dump(canonical, f)
    with open(os.path.join(out_dir, "auc.txt"), "w") as f:
        f.write(f"{ens_auc:.6f}\n")
    meta = {
        "retrained_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": int(len(df)),
        "n_train": int(split),
        "n_val": int(len(df) - split),
        "up_rate": round(up_rate, 4),
        "n_features": int(len(canonical)),
        "model_aucs": aucs,
        "ensemble_auc": round(float(ens_auc), 4),
        "val_frac": val_frac,
        "seed": seed,
        # 학습 데이터 구간 — champion_robust_eval 이 이 값으로 **학습구간과 겹치는 평가창을
        # 자동 제외**한다(2026-09-29 실측: 겹친 창 0.5883 vs 학습구간 밖 0.4914).
        "data_start": data_start,
        "data_end": data_end,
    }
    meta_path = os.path.join(out_dir, f"training-result-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info(
        "champion retrain done: n=%d up_rate=%.3f ensemble_auc=%.4f features=%d -> %s",
        len(df), up_rate, ens_auc, len(canonical), out_dir,
    )
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="Deterministic champion retrain")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--end-date", default=None,
                    help="학습 데이터 종료일(YYYY-MM-DD). 기본 None = 오늘(현행 동작). "
                         "고정하면 같은 구간으로 대조군/챌린저를 학습해 A/B 가 성립한다")
    ap.add_argument("--start-date", default=None,
                    help="학습 데이터 시작일. 기본 None = end_date - days")
    ap.add_argument("--stock-limit", type=int, default=200)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--n-estimators", type=int, default=500)
    ap.add_argument("--checkpoint-path", default=None,
                    help="부분 진척 체크포인트 경로(컨테이너에서 접근 가능한 절대경로). 주면 "
                         "500페어마다 rows.pkl+meta.json 으로 저장하고, 종목목록·start/end·"
                         "feature_engine 코드 mtime 이 같으면 다음 실행에서 이어받는다. "
                         "왜: 200종목×90일(실측 12,029페어) 빌드는 1.26페어/s 로 2.65h 가 걸려 "
                         "저녁 파이프라인 Phase 2 의 2h 캡(timeout 7200)에 매일 잘리고 **전량 "
                         "소실**됐다(2026-09-24 exit=124 · 2026-09-29 동일). 체크포인트를 주면 "
                         "재시도가 처음부터가 아니라 이어받기로 시작한다(단, --end-date 를 고정해야 "
                         "키가 유지된다 — 매일 '오늘'로 밀리면 체크포인트도 매일 폐기된다). "
                         "기본 None = 현행 동작(체크포인트 없음).")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("app.feature_engine.bayes_factor_features").setLevel(logging.ERROR)

    pg = _pg_connect()
    try:
        stocks = _select_stocks(pg, args.stock_limit)
        logger.info("selected %d stocks", len(stocks))
        pipeline = FeaturePipeline(pg_conn=pg)
        end = (datetime.strptime(args.end_date, "%Y-%m-%d") if args.end_date
               else datetime.now())
        start = (datetime.strptime(args.start_date, "%Y-%m-%d") if args.start_date
                 else end - timedelta(days=args.days))
        start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        logger.info("학습 데이터 구간 %s ~ %s", start_s, end_s)
        df = pipeline.build_training_features(stocks, start_s, end_s,
                                               checkpoint_path=args.checkpoint_path)
        if df is None or len(df) < 500:
            logger.error("insufficient panel rows: %s", 0 if df is None else len(df))
            return
        meta = retrain_champion(df, out_dir=args.out_dir,
                                val_frac=args.val_frac, n_estimators=args.n_estimators,
                                data_start=start_s, data_end=end_s)
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"CHAMPION -> {os.path.abspath(args.out_dir)}")

        # 2026-08: 재학습 결과 이력 기록 (Grafana Quant Strategy Monitoring — ML AUC 패널용)
        try:
            sys.path.insert(0, "/app/scripts")
            from record_strategy_run import record_run

            record_run(
                tool="model_retrain",
                stocks=len(stocks),
                auc=float(meta.get("ensemble_auc", 0) or 0),
                metric_value=float(meta.get("ensemble_auc", 0) or 0),
                meta={
                    "model_aucs": meta.get("model_aucs", {}),
                    "n_rows": meta.get("n_rows"),
                    "n_features": meta.get("n_features"),
                    "up_rate": meta.get("up_rate"),
                },
            )
        except Exception as _e:
            print(f"[record_run] model_retrain 기록 실패(무시): {_e}")
    finally:
        pg.close()


if __name__ == "__main__":
    main()
