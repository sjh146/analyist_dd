#!/usr/bin/env python3
"""KOSDAQ Swing Stock Screener — ML-powered stock discovery.

Loads trained EnsembleModel, builds features for all KOSDAQ stocks,
predicts up-probability, filters confidence >= 0.65, outputs Top 20
to console table + CSV.
"""

import sys
import os
import argparse
import logging
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))

import psycopg2
import numpy as np
import pandas as pd

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.scoring.effective_score import EffectiveScore, load_effective_scorer, score_and_filter_candidates
from app.uncertainty.gp_uncertainty import LOW_DIM_FEATURES

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

USE_EFFECTIVE_SCORE = os.environ.get("USE_EFFECTIVE_SCORE", "false").lower() in (
    "1", "true", "yes",
)


def parse_args():
    parser = argparse.ArgumentParser(description='Swing trade candidate screener')
    parser.add_argument('--include-krx-data', action='store_true',
                        help='Include KRX market data (foreign net buy, program trading, short selling) in scoring')
    parser.add_argument('--include-economic-events', action='store_true',
                        help='Include economic calendar impact in scoring')
    parser.add_argument('--output', type=str, default=None,
                        help='Output CSV path (default: data/ directory with date-based filename)')
    return parser.parse_args()


PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")
CONFIDENCE_THRESHOLD = 0.55


def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def get_kosdaq_stocks(pg_conn):
    """Get all KOSDAQ stocks with sufficient market data."""
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT s.stock_code, s.stock_name, COALESCE(s.sector, 'Unknown') as sector,
               MAX(md.trade_date) as latest_date
        FROM stocks s
        JOIN market_data md ON s.stock_code = md.stock_code
        WHERE s.market = 'KOSDAQ'
        GROUP BY s.stock_code, s.stock_name, s.sector
        HAVING COUNT(*) >= 20
        ORDER BY s.stock_code
    """)
    rows = cur.fetchall()
    cur.close()
    return rows  # [(code, name, sector, latest_date), ...]


def get_krx_foreign_net_buy(pg_conn, stock_code, lookback_days=5):
    """Get recent foreign net buy for a stock (positive = foreign buying)."""
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT SUM(foreign_net_buy) FROM foreign_institutional
            WHERE stock_code = %s AND trade_date >= CURRENT_DATE - %s
        """, (stock_code, lookback_days))
        result = cur.fetchone()
        cur.close()
        return float(result[0]) if result and result[0] else 0.0
    except Exception:
        return 0.0


def get_krx_program_trading(pg_conn, stock_code, lookback_days=5):
    """Get recent program trading net value for a stock."""
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT SUM(program_net) FROM program_trading
            WHERE stock_code = %s AND trade_date >= CURRENT_DATE - %s
        """, (stock_code, lookback_days))
        result = cur.fetchone()
        cur.close()
        return float(result[0]) if result and result[0] else 0.0
    except Exception:
        return 0.0


def get_krx_short_selling(pg_conn, stock_code, lookback_days=5):
    """Get recent short selling ratio (lower = more bullish)."""
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT AVG(short_selling_ratio) FROM short_selling
            WHERE stock_code = %s AND trade_date >= CURRENT_DATE - %s
        """, (stock_code, lookback_days))
        result = cur.fetchone()
        cur.close()
        return float(result[0]) if result and result[0] else 0.0
    except Exception:
        return 0.0


def get_economic_impact(pg_conn, lookahead_days=7):
    """Get max importance of upcoming economic events within lookahead_days."""
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT MAX(importance) FROM economic_calendar
            WHERE event_date BETWEEN CURRENT_DATE AND CURRENT_DATE + %s
              AND importance IS NOT NULL
        """, (lookahead_days,))
        result = cur.fetchone()
        cur.close()
        return int(result[0]) if result and result[0] else 0
    except Exception:
        return 0


def apply_krx_score_boost(candidates, pg_conn):
    """Apply score boost based on KRX data signals."""
    for c in candidates:
        code = c["stock_code"]
        foreign_net = get_krx_foreign_net_buy(pg_conn, code)
        program_net = get_krx_program_trading(pg_conn, code)
        short_ratio = get_krx_short_selling(pg_conn, code)

        boost = 1.0
        if foreign_net > 0:
            boost += 0.03
        if program_net > 0:
            boost += 0.02
        if short_ratio > 0 and short_ratio < 3.0:
            boost += 0.01

        c["confidence"] = min(round(c["confidence"] * boost, 4), 1.0)
        c["expected_return"] = round(
            (c["confidence"] - 0.5) * 2.0 * 100.0, 2
        )
    return candidates


def apply_economic_impact(candidates, pg_conn):
    """Adjust scores based on upcoming economic event importance."""
    impact = get_economic_impact(pg_conn)
    if impact >= 3:
        factor = 0.95
    elif impact == 2:
        factor = 0.98
    else:
        factor = 1.0

    if factor < 1.0:
        for c in candidates:
            c["confidence"] = min(round(c["confidence"] * factor, 4), 1.0)
            c["expected_return"] = round(
                (c["confidence"] - 0.5) * 2.0 * 100.0, 2
            )
    return candidates


def _collect_top_raw(stocks, pipeline, ensemble, feature_names, pg, top_n=20):
    """임계값 미달 시 상위 confidence 종목을 수집 (승률 추적용)."""
    import json as _json
    raw = []
    for code, name, sector, latest_date in stocks:
        try:
            features = pipeline.build_features(code, str(latest_date))
            if not features or features.get("feature_count", 0) < 10:
                continue
            fv = np.array(
                [float(features.get(f, 0.0)) for f in feature_names],
                dtype=np.float32,
            )
            fv = np.nan_to_num(fv, nan=0.0)
            prob = float(ensemble.predict(np.array([fv]))[0])
            raw.append({
                "stock_code": code,
                "stock_name": name,
                "sector": sector,
                "confidence": round(prob, 4),
                "expected_return": round((prob - 0.5) * 2.0 * 100.0, 2),
            })
        except Exception:
            continue
    raw.sort(key=lambda x: x["confidence"], reverse=True)
    return raw[:top_n]


def _build_low_dim_vector(features, low_dim_features):
    """Build the low-dim feature vector for the GP from a feature dict."""
    return np.array(
        [float(features.get(f, 0.0)) for f in low_dim_features],
        dtype=np.float64,
    )


def _load_effective_scorer():
    """Load a fitted calibrator + GP for effective_score, or a fallback scorer.

    Delegates to ``app.scoring.effective_score.load_effective_scorer`` which
    reads the artifacts persisted by ``app/training/fit_effective_score.py``
    (default ``app/models/effective_score/``, override with
    ``EFFECTIVE_SCORE_DIR``). When the artifacts are missing, returns an
    ``EffectiveScore`` with no components and the raw probability is used
    (with a warning) instead of crashing.
    """
    return load_effective_scorer()[0]


def main():
    args = parse_args()
    today = datetime.now().strftime("%Y-%m-%d")

    pg = get_pg_conn()
    stocks = get_kosdaq_stocks(pg)
    logger.info(f"Screening {len(stocks)} KOSDAQ stocks...")

    pipeline = FeaturePipeline(pg_conn=pg)
    # AUC 최고 모델 사용: auto_retrain이 champion을 최고 AUC로 유지
    # (champion_roc_auc >= challenger_roc_auc 시 승격) → champion 로드
    model_dir = os.environ.get("MODEL_DIR", "app/models/champion")
    ensemble = EnsembleModel(model_dir=model_dir)
    ensemble.load(model_dir)

    if not ensemble._is_trained:
        logger.error("No trained model found. Run training first.")
        pg.close()
        sys.exit(1)

    # USE_EFFECTIVE_SCORE 활성화: 오프라인 피팅 산출물 로드 (fit_effective_score.py)
    # + 베이지안 팩터 피처를 파이프라인에 주입 (핫루프에서 MCMC 금지 — 캐시된 사후분포만 사용)
    effective_scorer = None
    if USE_EFFECTIVE_SCORE:
        effective_scorer, bayes_factors, _scorer_meta = load_effective_scorer()
        if bayes_factors is not None:
            pipeline.bayes_factors = bayes_factors
            logger.info("Fitted BayesFactorFeatures injected — bayes_* features active")
        else:
            logger.warning("bayes_factors artifact not found — bayes_* features stay 0.0 defaults")

    candidates = []

    # 모델이 학습된 피처 목록 사용 — champion/feature_names.json (62개)이 정답
    # pipeline.get_feature_names()(149개)는 모델 학습 시점과 불일치 → XGBoostError
    feature_names = pipeline.get_feature_names()
    try:
        feature_names_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "services", "xgboost-ml", "app", "models", "champion",
            "feature_names.json",
        )
        if os.path.exists(feature_names_path):
            import json
            with open(feature_names_path) as f:
                feature_names = json.load(f)
            logger.info(f"Using champion feature_names.json: {len(feature_names)} features")
        else:
            logger.warning("feature_names.json 없음 — pipeline.get_feature_names() 사용")
    except Exception as e:
        logger.warning(f"feature_names.json 로드 실패 ({e}) — pipeline 사용")
    errors = 0
    raw_candidates = []

    # ── Pass 1: 전체 유니버스 피처 빌드 ─────────────────────────────
    # 크로스섹션 랭크(rank_*)는 같은 날짜의 전 종목 값이 필요 → 전 종목을 먼저
    # 빌드한 뒤 compute_cross_sectional_ranks()로 주입한다 (트레이너 정합).
    features_by_code = {}
    for i, (code, name, sector, latest_date) in enumerate(stocks):
        if (i + 1) % 100 == 0:
            logger.info(f"  Progress: {i + 1}/{len(stocks)}")

        try:
            features = pipeline.build_features(code, str(latest_date))
            if not features or features.get("feature_count", 0) < 10:
                continue
            features_by_code[code] = features
        except Exception as e:
            errors += 1
            if errors <= 3:
                logger.warning(f"Error building features {code}: {type(e).__name__}: {e}")
                import traceback
                logger.warning(traceback.format_exc()[-800:])
            continue

    logger.info(f"Features built for {len(features_by_code)} stocks (errors={errors})")
    pipeline.compute_cross_sectional_ranks(features_by_code)

    # ── Pass 2: 벡터 구성 + 예측 + 후보 수집 ────────────────────────
    raw_candidates = []
    for code, name, sector, latest_date in stocks:
        features = features_by_code.get(code)
        if features is None:
            continue
        try:
            feature_vector = np.array(
                [float(features.get(f, 0.0)) for f in feature_names],
                dtype=np.float32,
            )
            feature_vector = np.nan_to_num(feature_vector, nan=0.0)

            prob = float(ensemble.predict(np.array([feature_vector]))[0])

            raw_candidates.append({
                "stock_code": code,
                "stock_name": name,
                "sector": sector,
                "prob": prob,
                "low_dim_vec": (
                    _build_low_dim_vector(features, LOW_DIM_FEATURES)
                    if USE_EFFECTIVE_SCORE else None
                ),
            })
        except Exception as e:
            errors += 1
            if errors <= 3:
                logger.warning(f"Error scoring {code}: {type(e).__name__}: {e}")
                import traceback
                logger.warning(traceback.format_exc()[-800:])
            continue

    # 항상 유용한 Top-20을 출력한다: 절대 임계값(CONFIDENCE_THRESHOLD)은 소프트
    # 품질 게이트로 로그에만 기록하고, 활성 스코어(confidence/effective_score)
    # 기준 상위 20개를 보고한다. 저 base-rate 구간(예: 2026-08, 다음날 상승률
    # ~41%)에서는 0.55를 넘는 종목이 거의 없어, 임계값 필터만 쓰면 결과가 항상
    # 비거나 전부 하락 예측(raw 폴백)으로 나오는 문제를 방지한다.
    above_threshold = score_and_filter_candidates(
        raw_candidates, effective_scorer, USE_EFFECTIVE_SCORE, CONFIDENCE_THRESHOLD
    )
    candidates = score_and_filter_candidates(
        raw_candidates, effective_scorer, USE_EFFECTIVE_SCORE, 0.0
    )
    logger.info(
        "%d candidates above threshold %.2f — reporting top %d by active score",
        len(above_threshold), CONFIDENCE_THRESHOLD, min(20, len(candidates)),
    )

    # KRX/경제 보정은 상위 40개에만 적용 (전 종목 per-stock 쿼리 방지).
    if args.include_krx_data:
        logger.info("Applying KRX data score boost (top 40)...")
        head, rest = candidates[:40], candidates[40:]
        candidates = apply_krx_score_boost(head, pg) + rest

    if args.include_economic_events:
        logger.info("Applying economic calendar impact (top 40)...")
        head, rest = candidates[:40], candidates[40:]
        candidates = apply_economic_impact(head, pg) + rest

    pg.close()

    # 활성 키 기준 정렬 (flag on: effective_score, off: confidence)
    if USE_EFFECTIVE_SCORE:
        candidates.sort(key=lambda x: x["effective_score"], reverse=True)
    else:
        candidates.sort(key=lambda x: x["confidence"], reverse=True)
    top20 = candidates[:20]

    # Print table
    print(f"\nTop KOSDAQ Swing Candidates ({today})")
    print(f"{'Rank':<5} {'Code':<8} {'Name':<20} {'Sector':<15} {'Confidence':<12} {'Exp.Ret':<10}")
    print("-" * 70)

    if not top20:
        print(f"\n  No candidates meeting confidence threshold ({CONFIDENCE_THRESHOLD}).")
        print(f"  Model AUC=0.555 — consider retraining with more features/data.")
    else:
        for rank, c in enumerate(top20, 1):
            print(
                f"  {rank:<4} {c['stock_code']:<8} {c['stock_name']:<20} "
                f"{c['sector']:<15} {c['confidence']:<12.4f} +{c['expected_return']:.1f}%"
            )

    # Save CSV
    if top20:
        if args.output:
            csv_path = args.output
            csv_dir = os.path.dirname(csv_path)
            if csv_dir:
                os.makedirs(csv_dir, exist_ok=True)
        else:
            csv_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
            os.makedirs(csv_dir, exist_ok=True)
            csv_path = os.path.join(csv_dir, f"swing_candidates_{today}.csv")
        pd.DataFrame(top20).to_csv(csv_path, index=False)
        logger.info(f"CSV saved: {csv_path}")

    print(f"\nTotal screened: {len(stocks)}, Candidates: {len(candidates)}, Errors: {errors}")

    # 2026-08: 실행 결과 이력 기록 (Grafana Quant Strategy Monitoring 대시보드용)
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from record_strategy_run import record_run

        record_run(
            tool="swing_screener",
            status="ok" if errors == 0 else "partial",
            stocks=len(stocks),
            errors=errors,
            metric_value=top20[0]["expected_return"] if top20 else None,
            meta={
                "top20": [
                    {"code": c["stock_code"], "conf": round(float(c["confidence"]), 4),
                     "exp_ret": round(float(c["expected_return"]), 2)}
                    for c in top20
                ][:20],
            },
        )
    except Exception as _e:
        logger.warning(f"strategy_runs 기록 실패(무시): {_e}")


if __name__ == "__main__":
    main()
