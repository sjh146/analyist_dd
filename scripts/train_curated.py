#!/usr/bin/env python3
"""BC-250 (구 프로덕션) curated 레시피 재현 — 멀티시드 평균±std AUC 보고.

구 프로덕션(BC-250)에서 0.60+ test AUC 를 냈던 레시피를 현재 저장소/데이터에서
재현한다. 레시피 원본은 ``scripts/ml_infinite_loop.sh`` 의 ``train_v${VER}.py``
템플릿이며, 재현 단계는 아래와 같다.

1. 유니버스(결정적): KOSDAQ + trade_date >= '2026-04-01' + COUNT>=50,
   ``ORDER BY stock_code LIMIT 50``.
2. ``Trainer.prepare_training_data(stock_codes=..., days=180)`` (7개 반환).
3. train 라벨 오버샘플링으로 균형화(양성 부족분 복원추출, seed 고정).
4. curated 피처(``CORE_FEATURES`` 48개)와 ``feature_names`` 의 교집합만 사용.
   기본은 sentiment/news 5개 제외 -> 43개. ``--allow-sentiment`` 시 48개.
5. 오버샘플링된 train 을 67%/33% 로 쪼개 앞부분=학습, 뒷부분=validation.
6. HP override: learning_rate=0.03, depth=4, n_estimators=1500.
   (xgboost 는 ``max_depth``/``n_estimators`` 속성, lightgbm 은 ``max_depth``/
   ``n_estimators``, catboost 는 ``depth``/``iterations`` 키를 쓴다.)
7. ``X_test`` 예측 -> ``roc_auc_score`` (멀티시드 평균±std).

산출물은 ``champion_promote.py`` 호환: ``feature_names.json``(curated 순서),
``auc.txt``(평균 AUC), ``training-result-<ts>.json``, 세 모델 ``*_model.pkl``.
승격은 ``python -m app.training.champion_promote`` 로만 수행한다.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime

import numpy as np

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

logger = logging.getLogger(__name__)

CORE_FEATURES = [
    "ma_position_120", "ma_position_20", "ma_position_5", "ma_position_60",
    "net_income", "net_margin", "op_margin", "operating_profit", "price",
    "return_5d", "return_20d", "revenue",
    "similar_stocks_return_avg", "similar_stocks_return_std",
    "volatility_20d", "volatility_60d", "volume_ratio_20", "volume_ratio_5",
    "momentum_vs_volatility", "trend_interaction", "volume_price_trend",
    "cross_trend", "volatility_volume", "short_medium_term_momentum",
    "trend_confirmation", "price_volume",
    "return_5d_mean_10d", "volatility_20d_mean_10d", "volume_ratio_5_mean_10d",
    "rank_return_5d", "rank_return_20d", "rank_volatility_20d",
    "rank_volume_ratio_5", "rank_ma_position_5", "rank_volume_ratio_20",
    "target_ma_5", "target_ma_10", "target_ma_20",
    "momentum_1m_reverse", "quality_score",
    "kalman_momentum_1d", "kalman_momentum_5d", "kalman_volatility",
    "sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d",
    "news_count_5d", "news_count_20d",
]

SENTIMENT_NEWS_FEATURES = [
    "sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d",
    "news_count_5d", "news_count_20d",
]

DEFAULT_SEEDS = [0, 1, 2, 3, 4]
DEFAULT_OUT_DIR = "app/models/curated_cand"
DEFAULT_LIMIT = 50
DEFAULT_DAYS = 180
DEFAULT_LR = 0.03
DEFAULT_DEPTH = 4
DEFAULT_N_ESTIMATORS = 1500

# 유니버스 선택 기본값 — **현행 동작을 그대로 보존**한다(비교가능성).
# 확장은 명시적으로 넘긴다. 실측 근거: 기본값은 코드 알파벳 앞 50개(KOSDAQ)만 골라
# 패널이 13,609행/49종목에 묶여 있었다. DB 에는 2,428종목(250일 이상 + 일평균 거래대금 1억 이상)
# 이 있다 — 150종목 실험군 0.5727 vs 49종목군 0.5400.
UNIVERSE_DEFAULTS = {
    "market": "KOSDAQ",     # None 이면 KOSPI+KOSDAQ 전체
    "since": "2026-04-01",
    "min_days": 50,
    "min_value": 0.0,       # 일평균 거래대금 하한(원). 유동성 필터
    "order": "code",        # code(알파벳순, 현행) | value(거래대금 상위)
}

UNIVERSE_SQL = """
SELECT md.stock_code
FROM market_data md
JOIN stocks s ON md.stock_code = s.stock_code
WHERE (%(market)s IS NULL OR s.market = %(market)s)
  AND md.trade_date >= %(since)s
GROUP BY md.stock_code
HAVING COUNT(*) >= %(min_days)s
   AND AVG(COALESCE(NULLIF(md.trading_value, 0), 0)) >= %(min_value)s
ORDER BY CASE WHEN %(order)s = 'value'
              THEN AVG(COALESCE(NULLIF(md.trading_value, 0), 0)) END DESC NULLS LAST,
         md.stock_code
LIMIT %(limit)s
"""


def select_curated_features(feature_names, allow_sentiment=False):
    """core 48개와 feature_names 의 교집합. allow_sentiment=False 면 sentiment/news 제외."""
    core = CORE_FEATURES if allow_sentiment else [
        f for f in CORE_FEATURES if f not in SENTIMENT_NEWS_FEATURES
    ]
    return [f for f in core if f in feature_names]


def oversample_balance(X, y, rng):
    """train 라벨 오버샘플링: 양성 부족분을 복원추출해 균형화 (seed 고정 rng)."""
    y = np.asarray(y).astype(int)
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos > 0 and n_pos < n_neg:
        pos_idx = np.where(y == 1)[0]
        oversampled = rng.choice(pos_idx, size=n_neg - n_pos, replace=True)
        balanced = np.concatenate([np.arange(len(y)), oversampled])
        rng.shuffle(balanced)
        return X[balanced], y[balanced]
    return X, y


def split_train_val(X, y, train_frac=0.67):
    """앞부분=train, 뒷부분=validation (시간순 유지)."""
    n = len(X)
    cut = int(n * train_frac)
    return X[:cut], y[:cut], X[cut:], y[cut:]


def aggregate_seeds(aucs):
    """시드별 AUC dict -> (평균, 표준편차). 단일 시드면 std=0.0."""
    vals = np.asarray([float(aucs[k]) for k in aucs], dtype=np.float64)
    mean = float(vals.mean())
    std = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
    return mean, std


def build_result_dict(
    ensemble_auc, model_aucs, n_rows, n_features, up_rate,
    seeds, seed_aucs, auc_mean, auc_std, config,
):
    """champion_promote.py 호환 training-result dict."""
    return {
        "ensemble_auc": round(float(ensemble_auc), 6),
        "model_aucs": {k: round(float(v), 6) for k, v in model_aucs.items()},
        "n_rows": int(n_rows),
        "n_features": int(n_features),
        "up_rate": round(float(up_rate), 4),
        "seeds": [int(s) for s in seeds],
        "seed_aucs": {str(s): round(float(a), 6) for s, a in seed_aucs.items()},
        "auc_mean": round(float(auc_mean), 6),
        "auc_std": round(float(auc_std), 6),
        "retrained_at": datetime.now().isoformat(timespec="seconds"),
        "config": config,
    }


def apply_hyperparams(ensemble, lr, depth, n_estimators, seed):
    """EnsembleModel 내부 모델에 HP + seed 전파.

    xgboost: ``max_depth`` + ``self.n_estimators``(num_boost_round) + ``random_state``.
    lightgbm: ``max_depth`` + ``n_estimators`` + ``random_state``.
    catboost: ``depth`` + ``iterations`` + ``random_seed``.
    """
    for model in ensemble.models:
        params = getattr(model, "params", None)
        if params is None:
            continue
        if "random_state" in params:
            params["random_state"] = seed
        if "random_seed" in params:
            params["random_seed"] = seed
        params["learning_rate"] = lr
        if "max_depth" in params:
            params["max_depth"] = depth
        elif "depth" in params:
            params["depth"] = depth
        # 템플릿 그대로: 'n_estimators' 키를 가진 모델(lightgbm)에만 override.
        # xgboost 는 self.n_estimators(기본 800), catboost 는 'iterations'(기본 300) 유지.
        if "n_estimators" in params:
            params["n_estimators"] = n_estimators


def _safe_auc(y_true, probs):
    from sklearn.metrics import roc_auc_score

    try:
        return float(roc_auc_score(y_true, probs))
    except ValueError:
        return 0.5


def train_one_seed(X_train, X_val, X_test, y_train, y_val, y_test,
                   feature_names, feature_list, out_dir, seed, lr, depth,
                   n_estimators):
    """단일 시드 학습 -> (ensemble_test_auc, per_model_test_aucs, feature_list, ensemble)."""
    from app.models.ensemble_model import EnsembleModel

    idx = [feature_names.index(f) for f in feature_list]
    X_train_c = X_train[:, idx]
    X_val_c = X_val[:, idx]
    X_test_c = X_test[:, idx]

    rng = np.random.default_rng(seed)
    X_bal, y_bal = oversample_balance(X_train_c, y_train, rng)
    # 템플릿 그대로: 오버샘플링된 train 을 67%/33% 로 쪼개 앞 67% 만 학습에 쓰고
    # 뒷 33% 는 사용하지 않는다. validation 은 prepare_training_data 의
    # 시계열 val(X_val)을 그대로 쓴다(오버샘플링된 꼬리를 val 로 쓰면
    # 양성 중복으로 val AUC 가 부풀고 과적합).
    cut = int(len(X_bal) * 0.67)
    Xc_t, yc_t = X_bal[:cut], y_bal[:cut]

    ensemble = EnsembleModel(model_dir=out_dir)
    apply_hyperparams(ensemble, lr, depth, n_estimators, seed)
    ensemble.train(Xc_t, yc_t, X_val_c, y_val)

    test_probs = ensemble.predict(X_test_c)
    ens_auc = _safe_auc(y_test, test_probs)

    model_aucs = {}
    for name, model in zip(ensemble.model_names, ensemble.models):
        try:
            model_aucs[name] = _safe_auc(y_test, model.predict(X_test_c))
        except Exception as exc:
            logger.warning("seed %d %s test AUC failed: %s", seed, name, exc)
            model_aucs[name] = 0.5

    return ens_auc, model_aucs, feature_list, ensemble


def _connect_pg():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _select_universe(pg, limit, market=UNIVERSE_DEFAULTS["market"],
                     since=UNIVERSE_DEFAULTS["since"], min_days=UNIVERSE_DEFAULTS["min_days"],
                     min_value=UNIVERSE_DEFAULTS["min_value"], order=UNIVERSE_DEFAULTS["order"]):
    """유니버스 선택. **기본값은 현행 동작 그대로**(KOSDAQ·코드순·최소 50일)다.

    확장 예 (500종목, KOSPI 포함, 유동성 상위, 250일 이상 이력):
      _select_universe(pg, 500, market=None, since="2025-07-01",
                       min_days=250, min_value=1e8, order="value")

    WHY 기본값 보존: 기존 학습·측정 결과와 비교가능성이 깨지면 "개선"인지 "표본 교체"인지
    구분할 수 없다. 유니버스를 바꿀 때는 **새 패널 파일명**을 함께 바꿔 캐시 충돌도 피한다.
    """
    cur = pg.cursor()
    cur.execute(UNIVERSE_SQL, {"limit": limit, "market": market, "since": since,
                               "min_days": min_days, "min_value": min_value,
                               "order": order})
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    return codes


def _load_or_prepare(stock_codes, days, out_dir, pg, use_cache=True):
    """prepare_training_data 결과를 npz 로 캐시 (feature 재구축 30분+ 회피)."""
    cache = os.path.join(out_dir, "panel_cache.npz")
    if use_cache and os.path.exists(cache):
        logger.info("loading cached panel: %s", cache)
        d = np.load(cache, allow_pickle=True)
        return (d["X_train"], d["X_val"], d["X_test"], d["y_train"],
                d["y_val"], d["y_test"], list(d["feature_names"]))

    from app.feature_engine.feature_pipeline import FeaturePipeline
    from app.training.trainer import Trainer

    pipeline = FeaturePipeline(pg_conn=pg)
    trainer = Trainer(storage=None, feature_pipeline=pipeline)
    result = trainer.prepare_training_data(stock_codes=stock_codes, days=days)
    X_train, X_val, X_test, y_train, y_val, y_test, feature_names = result
    if X_train is None:
        return None

    np.savez(cache, X_train=X_train, X_val=X_val, X_test=X_test,
             y_train=y_train, y_val=y_val, y_test=y_test,
             feature_names=np.array(feature_names, dtype=object))
    logger.info("cached panel -> %s", cache)
    return (X_train, X_val, X_test, y_train, y_val, y_test, feature_names)


def main():
    ap = argparse.ArgumentParser(description="BC-250 curated recipe 재현")
    ap.add_argument("--seeds", default=",".join(str(s) for s in DEFAULT_SEEDS),
                    help="쉼표 구분 시드 목록 (기본 0,1,2,3,4)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--lr", type=float, default=DEFAULT_LR)
    ap.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    ap.add_argument("--n-estimators", type=int, default=DEFAULT_N_ESTIMATORS)
    ap.add_argument("--allow-sentiment", action="store_true",
                    help="구 v15 처럼 sentiment/news 피처 포함")
    ap.add_argument("--all-features", action="store_true",
                    help="(진단용) curated 대신 prepare_training_data 가 내보낸 전체 피처 사용")
    ap.add_argument("--no-cache", action="store_true",
                    help="패널 캐시 무시하고 feature 재구축")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("app.feature_engine.bayes_factor_features").setLevel(logging.ERROR)

    seeds = [int(s) for s in args.seeds.split(",") if s.strip() != ""]
    if not seeds:
        logger.error("no seeds provided")
        return 2

    os.makedirs(args.out_dir, exist_ok=True)

    pg = _connect_pg()
    try:
        stock_codes = _select_universe(pg, args.limit)
        logger.info("universe: %d KOSDAQ stocks", len(stock_codes))

        prepared = _load_or_prepare(stock_codes, args.days, args.out_dir, pg,
                                    use_cache=not args.no_cache)
        if prepared is None:
            logger.error("prepare_training_data failed")
            return 1
        X_train, X_val, X_test, y_train, y_val, y_test, feature_names = prepared

        logger.info("data: train=%s val=%s test=%s features=%d",
                    X_train.shape, X_val.shape, X_test.shape, len(feature_names))

        if args.all_features:
            feature_list = list(feature_names)
        else:
            feature_list = select_curated_features(feature_names, args.allow_sentiment)
        logger.info("feature list: %d (all_features=%s allow_sentiment=%s)",
                    len(feature_list), args.all_features, args.allow_sentiment)

        seed_aucs = {}
        model_aucs_sum = {}
        saved_ensemble = None
        for seed in seeds:
            ens_auc, m_aucs, feature_list_seed, ensemble = train_one_seed(
                X_train, X_val, X_test, y_train, y_val, y_test,
                feature_names, feature_list, args.out_dir, seed, args.lr,
                args.depth, args.n_estimators,
            )
            seed_aucs[seed] = ens_auc
            for name, a in m_aucs.items():
                model_aucs_sum.setdefault(name, []).append(a)
            logger.info("seed %d: test AUC=%.4f", seed, ens_auc)
            if saved_ensemble is None:
                saved_ensemble = ensemble

        mean_auc, std_auc = aggregate_seeds(seed_aucs)
        model_aucs = {name: float(np.mean(v)) for name, v in model_aucs_sum.items()}

        n_rows = int(len(y_test))
        up_rate = float(y_test.mean())

        logger.info("multi-seed: mean=%.4f std=%.4f seeds=%s",
                    mean_auc, std_auc, seed_aucs)

        saved_ensemble.save(args.out_dir)
        saved_ensemble.save_feature_names(feature_list, args.out_dir)
        with open(os.path.join(args.out_dir, "auc.txt"), "w") as f:
            f.write(f"{mean_auc:.6f}\n")

        config = (
            f"curated lr={args.lr} depth={args.depth} est={args.n_estimators} "
            f"sentiment={'on' if args.allow_sentiment else 'off'} "
            f"all_features={args.all_features} "
            f"days={args.days} limit={args.limit}"
        )
        meta = build_result_dict(
            ensemble_auc=mean_auc,
            model_aucs=model_aucs,
            n_rows=n_rows,
            n_features=len(feature_list),
            up_rate=up_rate,
            seeds=seeds,
            seed_aucs=seed_aucs,
            auc_mean=mean_auc,
            auc_std=std_auc,
            config=config,
        )
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        meta_path = os.path.join(args.out_dir, f"training-result-{ts}.json")
        with open(meta_path, "w") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        print(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"RESULT mean_auc={mean_auc:.4f} std_auc={std_auc:.4f} "
              f"n_features={len(feature_list)} n_rows={n_rows} -> {os.path.abspath(args.out_dir)}")

        if mean_auc >= 0.60 and std_auc <= 0.02:
            print(f"PASS: mean AUC {mean_auc:.4f} >= 0.60 and std {std_auc:.4f} <= 0.02")
            return 0
        print(f"FAIL: mean AUC {mean_auc:.4f} (>=0.60) or std {std_auc:.4f} (<=0.02) not met; "
              f"seed_aucs={seed_aucs}")
        return 1
    finally:
        pg.close()


if __name__ == "__main__":
    sys.exit(main())
