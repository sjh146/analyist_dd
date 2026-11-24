"""
Feature Pipeline
Orchestrates feature extraction from all data sources into a single feature dict/DataFrame.
"""

import pandas as pd
import numpy as np
import json
import atexit
import logging
import os
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from app.feature_engine.market_features import MarketFeatures
from app.feature_engine.company_features import CompanyFeatures
from app.feature_engine.sentiment_features import SentimentFeatures
from app.feature_engine.macro_features import MacroFeatures
from app.feature_engine.graph_features import GraphFeatures
from app.feature_engine.vector_features import VectorFeatures
from app.feature_engine.feature_store import FeatureStore
from app.feature_engine.market_data_filter import MARKET_DATA_VALID
from app.feature_engine.factor_features import FactorFeatures
from app.feature_engine.scorer import QualityScorer
from app.feature_engine.kalman_filter import KalmanFeatureFilter
from app.feature_engine.bayes_factor_features import BayesFactorFeatures
from app.feature_engine.news_event_features import NewsEventFeatures
from app.feature_engine.sns_feature_bundle import SnsFeatureBundle
from app.feature_engine.sns_feature_bundle import feature_names as sns_feature_names
from app.feature_engine.intraday_features import IntradayFeatures
from app.feature_engine.intraday_features import feature_names as intraday_feature_names
from app.feature_engine.us_market_features import (
    UsMarketFeatures,
    feature_names as us_market_feature_names,
)

logger = logging.getLogger(__name__)

_MISS: Dict[str, int] = {}


def _note_miss(feature: str, exc: BaseException) -> None:
    """피처 조회 실패를 세어 두고, 같은 사유는 한 번만 WARNING 으로 남긴다."""
    key = "{0}:{1}".format(feature, type(exc).__name__)
    _MISS[key] = _MISS.get(key, 0) + 1
    if _MISS[key] == 1:
        logger.warning("피처 결측 %s — %s: %s (동일 사유는 집계만)",
                       feature, type(exc).__name__, str(exc)[:160])


def feature_miss_report() -> Dict[str, int]:
    return dict(_MISS)


def _dump_miss_report() -> None:
    if not _MISS:
        return
    try:
        import json
        import os
        out = os.environ.get("FEATURE_MISS_REPORT", "/app/data/state/feature_miss_report.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(_MISS, fh, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception as exc:
        logger.warning("피처 결측 리포트 저장 실패: %s", exc)


atexit.register(_dump_miss_report)


class FeaturePipeline:
    """Builds complete feature sets from market, company, sentiment, macro, graph, and vector data."""

    def __init__(self, pg_conn=None, neo4j_conn=None, use_feature_store=False,
                 feature_store: Optional[FeatureStore] = None,
                 feature_store_manifest: Optional[str] = None):
        self.market = MarketFeatures()
        self.factors = FactorFeatures()
        self.company = CompanyFeatures()
        self.sentiment = SentimentFeatures()
        self.macro = MacroFeatures()
        self.graph = GraphFeatures()
        self.vector = VectorFeatures()
        self.scorer = QualityScorer()
        self.kalman = KalmanFeatureFilter()
        self.bayes_factors = BayesFactorFeatures()
        self.news_events = NewsEventFeatures()



        self.use_intraday = str(os.environ.get("INTRADAY_FEATURES", "")).strip().lower() in (
            "1", "true", "yes", "on")
        self.intraday = IntradayFeatures() if self.use_intraday else None



        self.use_us_market = str(os.environ.get("US_MARKET_FEATURES", "")).strip().lower() in (
            "1", "true", "yes", "on")
        self.us_market = UsMarketFeatures() if self.use_us_market else None
        self.pg_conn = pg_conn
        self.neo4j_conn = neo4j_conn
        if self.neo4j_conn is None:


            self.neo4j_conn = self._connect_neo4j()
        self._cache = {}
        self._cache_ttl = 3600
        self.use_feature_store = use_feature_store


        self.feature_store = feature_store or (
            FeatureStore(pg_conn=self.pg_conn, manifest_path=feature_store_manifest)
            if use_feature_store else None)

    def build_features(
        self, stock_code: str, date: str = None,
        market_df: pd.DataFrame = None,
    ) -> Dict:
        """Build complete feature set (~58 features) for a single stock on a given date."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        cache_key = f"{stock_code}:{date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if self.use_feature_store and self.feature_store is not None:
            try:
                stored = self.feature_store.load_features(stock_code, date)
                if stored:
                    stored["feature_count"] = len(stored)
                    stored["stock_code"] = stock_code
                    stored["date"] = date
                    self._cache[cache_key] = stored
                    return stored
            except Exception:
                logger.exception("FeatureStore load failed; falling back to compute")

        features = {}


        if market_df is None or market_df.empty:
            if self.pg_conn is not None:
                try:
                    cur = self.pg_conn.cursor()
                    cur.execute(f"""
                        SELECT trade_date, open_price, high_price, low_price, close_price, volume
                        FROM market_data
                        WHERE stock_code = %s AND trade_date <= %s
                          AND {MARKET_DATA_VALID}
                        ORDER BY trade_date DESC
                        LIMIT 250
                    """, (stock_code, date))
                    rows = cur.fetchall()
                    cur.close()
                    if rows:
                        market_df = pd.DataFrame(rows, columns=['trade_date', 'open', 'high', 'low', 'close', 'volume'])
                        market_df = market_df.sort_values('trade_date').reset_index(drop=True)
                except Exception as e:
                    _note_miss("group:market_data", e)
                    self.pg_conn.rollback()
                    market_df = pd.DataFrame()


        if market_df is not None and not market_df.empty and date is not None:
            if 'trade_date' in market_df.columns:
                market_df = market_df.copy()
                mkt_dates = pd.to_datetime(market_df['trade_date'])
                cutoff = pd.Timestamp(date)
                market_df = market_df[mkt_dates <= cutoff].reset_index(drop=True)

        features.update(self.market.get_all_features(
            market_df if market_df is not None and not market_df.empty else pd.DataFrame(),
            stock_code, self.pg_conn,
        ))


        if market_df is not None and not market_df.empty:
            close_s = market_df.get("close_price", market_df.get("close"))
            if close_s is not None and len(close_s) > 0:
                close_arr = close_s.values if hasattr(close_s, 'values') else np.array(close_s)
                features.update(self.kalman.smooth_returns(close_arr))


        if market_df is not None and not market_df.empty:
            close_s = market_df.get("close_price", market_df.get("close"))
            if close_s is not None and len(close_s) > 0:
                close_arr = close_s.values if hasattr(close_s, 'values') else np.array(close_s)
                features.update(self.bayes_factors.compute(close_arr))




        features.update(self.factors.get_all_factors(stock_code, market_df, self.pg_conn,
                                                     date=str(date)))

        features.update(self.company.get_all_features(stock_code, self.pg_conn, str(date)))

        features.update(self.sentiment.get_all_features(stock_code, self.pg_conn, str(date)))




        _ev_date = datetime.strptime(str(date)[:10], "%Y-%m-%d").date()
        features.update(self.news_events.get_all_features(stock_code, self.pg_conn, _ev_date))



        try:
            bundle = getattr(self, "_sns_bundle", None)
            if bundle is None or getattr(bundle, "pg_conn", None) is not self.pg_conn:
                bundle = self._sns_bundle = SnsFeatureBundle(pg_conn=self.pg_conn)
            features.update(bundle.load(stock_code, date=str(date)))
        except Exception as e:
            _note_miss("group:sns_features", e)


        sentiment = self._get_stock_sentiment(stock_code, date)
        features.update(sentiment)


        if self.intraday is not None and self.pg_conn is not None:
            features.update(self.intraday.get_all_features(stock_code, self.pg_conn, str(date)[:10]))


        if self.us_market is not None and self.pg_conn is not None:
            features.update(self.us_market.get_all_features(self.pg_conn, str(date)[:10]))


        features["quality_score"] = self.scorer.get_f_score(stock_code, self.pg_conn, date=str(date))

        features.update(self.macro.get_all_features(self.pg_conn, str(date)))


        economic = self._get_economic_events(date)
        features.update(economic)

        features.update(self.graph.get_graph_features(stock_code, self.neo4j_conn, str(date)))

        features.update(self.vector.get_vector_features_from_db(stock_code, self.pg_conn, str(date)))

        try:
            features.update(self._build_advanced_features(stock_code, date, market_df))
        except Exception as e:
            _note_miss("group:advanced_features", e)






        try:
            self._build_trainer_consistent_features(features, market_df)
        except Exception as e:
            _note_miss("group:trainer_features", e)

        features["feature_count"] = len(features)
        features["stock_code"] = stock_code
        features["date"] = date

        self._cache[cache_key] = features

        if self.use_feature_store and self.feature_store is not None:
            try:
                self.feature_store.save_features(stock_code, date, features)
            except Exception:
                logger.exception("FeatureStore save failed; continuing")

        return features

    def _build_trainer_consistent_features(
        self, features: Dict, market_df: pd.DataFrame = None,
    ) -> None:
        """Replicate the engineered features ``Trainer.prepare_training_data``
        adds (trainer.py:67-134) so INFERENCE matches TRAINING.

        Without these, the champion model receives 0.0 for ~20 of its 62
        features at inference and collapses to a near-constant prediction
        (hit Aug 2026: every KOSDAQ stock predicted DOWN, up:0/down:1668).

        This method covers the per-row pieces (interactions, rolling means,
        target MAs). The cross-sectional ``rank_*`` features are injected by
        ``compute_cross_sectional_ranks`` once the whole universe is built
        (they need the per-date cross-section, see swing_screener main()).

        Mutates ``features`` in place. Failures degrade to the existing values
        (0.0 fills) rather than crashing the pipeline.
        """

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
            fa = float(features.get(a, 0.0) or 0.0)
            fb = float(features.get(b, 0.0) or 0.0)
            features[name] = fa * fb




        if market_df is None or market_df.empty:
            return
        close_s = market_df.get("close_price", market_df.get("close"))
        if close_s is None or len(close_s) < 5:
            return
        try:
            close = pd.Series(
                [float(c) for c in close_s], dtype=np.float64
            ).reset_index(drop=True)
            rets = close.pct_change()
            ret_5d = close.pct_change(5)
            ret_20d = close.pct_change(20)

            vol20 = rets.rolling(20, min_periods=2).std(ddof=0)

            vol_s = market_df.get("volume")
            volr5 = None
            if vol_s is not None and len(vol_s) >= 5:
                vol = pd.Series([float(v) for v in vol_s], dtype=np.float64).reset_index(drop=True)
                volr5 = vol / vol.rolling(5, min_periods=1).mean()

            def _last(s: pd.Series, default: float = 0.0) -> float:
                v = s.iloc[-1] if len(s) > 0 else default
                return float(v) if pd.notna(v) else default

            features["return_5d_mean_10d"] = _last(ret_5d.rolling(10, min_periods=1).mean())
            features["volatility_20d_mean_10d"] = _last(vol20.rolling(10, min_periods=1).mean())
            features["volume_ratio_5_mean_10d"] = (
                _last(volr5.rolling(10, min_periods=1).mean()) if volr5 is not None else 0.0
            )
            features["target_ma_5"] = _last(rets.rolling(5, min_periods=1).mean())
            features["target_ma_10"] = _last(rets.rolling(10, min_periods=1).mean())
            features["target_ma_20"] = _last(rets.rolling(20, min_periods=1).mean())
        except Exception as e:  # pragma: no cover - defensive
            _note_miss("group:trainer_rolling", e)

    def compute_cross_sectional_ranks(self, features_by_code: Dict[str, Dict]) -> None:
        """Inject ``rank_*`` features — percentile rank within each date's
        cross-section — replicating ``Trainer.prepare_training_data``
        (trainer.py:107-118). Mutates the feature dicts in place.

        Must be called AFTER features are built for the whole universe
        (the screener's first pass) because a rank needs all stocks on the
        same date. Columns ranked: return_5d, return_20d, volatility_20d,
        volume_ratio_5, ma_position_5, volume_ratio_20.
        """
        rank_cols = [
            "return_5d", "return_20d", "volatility_20d",
            "volume_ratio_5", "ma_position_5", "volume_ratio_20",
        ]
        by_date: Dict[str, list] = {}
        for code, feats in features_by_code.items():
            by_date.setdefault(str(feats.get("date", "")), []).append(feats)

        for _date, group in by_date.items():
            for col in rank_cols:
                vals = pd.Series(
                    [float(f.get(col, 0.0) or 0.0) for f in group], dtype=np.float64
                )
                ranks = vals.rank(pct=True)
                for feats, r in zip(group, ranks):
                    feats[f"rank_{col}"] = float(r)

    def build_training_features(
        self, stock_codes: List[str], start_date: str, end_date: str,
        checkpoint_path: Optional[str] = None, checkpoint_every: int = 500,
        resume: bool = True, feature_store_manifest: Optional[str] = None,
    ) -> pd.DataFrame:
        """Build feature matrix for model training across multiple stocks and dates.

        checkpoint_path (선택): 주면 **부분 진척을 주기적으로 디스크에 저장**하고, 다음 실행에서
        이어서 빌드한다. 왜 필요한가 — 실측 2026-09-25: 150종목 패널 빌드가 30,000/41,893
        (71.6%)·4시간14분 지점에서 평일 20:00 크론(scripts/full_pipeline_dd.sh 의
        `docker compose up -d`)의 컨테이너 재생성으로 SIGKILL(137) 되어 **전량 소실**됐다.
        진척은 빌드 끝에 1회만 저장되는 구조라 부분 진척이 남지 않았다.
        체크포인트는 (유니버스, 구간, feature_engine 코드 mtime)이 모두 같을 때만 재사용한다 —
        피처 코드가 바뀌면 옛 행과 새 행이 섞이는 것을 막는다.
        """
        if self.use_feature_store and self.feature_store is not None:





            if feature_store_manifest:
                self.feature_store.manifest_path = feature_store_manifest

        rows = []

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute(f"""
                    SELECT stock_code, trade_date::text
                    FROM market_data
                    WHERE stock_code = ANY(%s)
                      AND trade_date >= %s AND trade_date <= %s
                      AND {MARKET_DATA_VALID}
                    ORDER BY stock_code, trade_date
                """, (stock_codes, start_date, end_date))
                available = cur.fetchall()
                cur.close()

                from collections import defaultdict
                dates_by_stock = defaultdict(list)
                for code, dt in available:
                    dates_by_stock[code].append(dt)
            except Exception as e:
                logger.warning(f"Failed to query available dates: {e}")
                dates_by_stock = {}
        else:
            dates_by_stock = {}


        if self.use_feature_store and self.feature_store is not None:
            expected = {(str(c), str(d)[:10])
                        for c, ds in dates_by_stock.items() for d in ds}
            try:
                stored = self.feature_store.load_batch(
                    stock_codes, start_date, end_date, expected_keys=expected)
                if not stored.empty:
                    logger.info(f"FeatureStore 적중: {len(stored)}행 (재계산 생략) "
                                f"keys={len(expected)}")
                    return stored
                logger.info(f"FeatureStore 미적중({len(expected)}키) — 재계산 경로로: "
                            f"{getattr(self.feature_store, 'last_refusal', None)}")
            except Exception:
                logger.exception("FeatureStore batch load failed; falling back to per-stock compute")


        market_data_by_stock = {}
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()

                lookback_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
                cur.execute(f"""
                    SELECT stock_code, trade_date, open_price, high_price, low_price, close_price, volume
                    FROM market_data
                    WHERE stock_code = ANY(%s)
                      AND trade_date >= %s AND trade_date <= %s
                      AND {MARKET_DATA_VALID}
                    ORDER BY stock_code, trade_date
                """, (stock_codes, lookback_start, end_date))
                all_rows = cur.fetchall()
                cur.close()


                stock_data = defaultdict(list)
                for code, tdate, open_p, high_p, low_p, close_p, vol in all_rows:
                    stock_data[code].append({
                        'trade_date': tdate, 'open': open_p, 'high': high_p,
                        'low': low_p, 'close': close_p, 'volume': vol,
                    })

                for code, rows_list in stock_data.items():
                    df = pd.DataFrame(rows_list)
                    df = df.sort_values('trade_date').reset_index(drop=True)
                    market_data_by_stock[code] = df

                logger.info(f"Batch-loaded market data for {len(market_data_by_stock)} stocks ({len(all_rows)} rows)")
            except Exception as e:
                logger.warning(f"Batch market data load failed: {e}")
                market_data_by_stock = {}

        total_pairs = sum(len(d) for d in dates_by_stock.values())
        processed = 0
        done_keys: set = set()
        ck_meta = f"{checkpoint_path}.meta.json" if checkpoint_path else None
        ck_rows = f"{checkpoint_path}.rows.pkl" if checkpoint_path else None
        code_sig = self._feature_code_sig()

        if (checkpoint_path and ck_meta and ck_rows and resume
                and os.path.exists(ck_meta) and os.path.exists(ck_rows)):
            try:
                with open(ck_meta, encoding="utf-8") as f:
                    meta = json.load(f)
                same = (list(meta.get("stock_codes") or []) == list(stock_codes)
                        and meta.get("start_date") == start_date
                        and meta.get("end_date") == end_date
                        and meta.get("code_sig") == code_sig)
                if same:


                    _ckdf = pd.read_pickle(ck_rows)
                    rows = _ckdf.to_dict("records") if isinstance(_ckdf, pd.DataFrame) else list(_ckdf)
                    done_keys = set(meta.get("done_keys") or [])
                    processed = int(meta.get("processed") or 0)
                    logger.info(
                        f"체크포인트 재개: processed={processed}/{total_pairs} "
                        f"({100.0 * processed / max(1, total_pairs):.1f}%) rows={len(rows)}"
                        f" — {ck_meta} (updated {meta.get('updated_at')})")
                else:
                    logger.info("체크포인트 무시: 유니버스/구간/피처코드가 달라짐 — 처음부터 빌드")
                    rows, done_keys, processed = [], set(), 0
            except Exception as e:
                logger.warning(f"체크포인트 로드 실패({type(e).__name__}: {e}) — 처음부터 빌드")
                rows, done_keys, processed = [], set(), 0

        t0 = time.time()
        processed0 = processed
        since_ck = 0
        for code in stock_codes:



            self._assert_code_unchanged(code_sig, processed, total_pairs, ck_rows, ck_meta)
            stock_dates = dates_by_stock.get(code, [])
            if not stock_dates:
                continue
            stock_market_df = market_data_by_stock.get(code, pd.DataFrame())
            for i, date_str in enumerate(stock_dates):
                if done_keys and f"{code}|{date_str}" in done_keys:
                    continue
                try:
                    features = self.build_features(code, date_str, market_df=stock_market_df)
                    if features.get("feature_count", 0) >= 10:
                        rows.append(features)
                    processed += 1
                    done_keys.add(f"{code}|{date_str}")
                    since_ck += 1
                    if processed % 200 == 0:
                        el = max(1e-9, time.time() - t0)
                        rate = (processed - processed0) / el
                        eta_min = (total_pairs - processed) / rate / 60.0 if rate > 0 else float("inf")
                        logger.info(
                            f"Build progress: {processed}/{total_pairs} stock-date pairs "
                            f"({100.0 * processed / max(1, total_pairs):.1f}%) "
                            f"{rate:.2f} pair/s ETA {eta_min:.0f}min")


                    if checkpoint_path and since_ck >= checkpoint_every:
                        since_ck = 0
                        self._save_checkpoint(ck_rows, ck_meta, rows, done_keys,
                                              processed, total_pairs, stock_codes,
                                              start_date, end_date, code_sig)
                except Exception as e:
                    logger.debug(f"Feature build failed for {code} {date_str}: {e}")
                    continue

        return pd.DataFrame(rows) if rows else pd.DataFrame()

    @staticmethod
    def _feature_code_sig():
        """feature_engine 패키지 .py 의 최신 mtime — 피처 코드가 바뀌면 체크포인트를 무효화한다."""
        try:
            d = os.path.dirname(os.path.abspath(__file__))
            return round(max(os.path.getmtime(os.path.join(d, f))
                             for f in os.listdir(d) if f.endswith(".py")), 3)
        except Exception:
            return None

    def _assert_code_unchanged(self, code_sig, processed, total_pairs,
                               ck_rows=None, ck_meta=None):
        """빌드 **중** 피처 코드가 바뀌면 그 자리에서 중단한다(오염 패널은 저장하지 않는다).

        실측(2026-09-29 U3): 종전 검사는 빌드 **끝**(wf_wave.build_panel L209-218)에만 있어
        354분·32,576/32,576(100%)을 다 채운 뒤 저장을 거부했다 → 하룻밤(5.9시간) 전량 소실.
        09-27 에는 같은 원인으로 47% 지점 소실이 있었다. 검사를 종목 배치마다 돌리면 몇 분 안에
        멈춰 남은 시간을 아낀다 — 오염 패널을 저장하지 않는다는 목적은 동일하다(앞부분 옛 코드·
        뒷부분 새 코드가 섞이면 결측 패턴이 종목/기간과 상관돼 '종목 식별 증폭' 유사누수가 난다).
        체크포인트도 버린다(meta.code_sig 가 이미 무효라 재개해도 어차피 "체크포인트 무시"된다).
        """
        if code_sig is None:
            return
        now_sig = self._feature_code_sig()
        if now_sig is None or now_sig == code_sig:
            return
        for p in (ck_rows, ck_meta):
            try:
                if p:
                    os.remove(p)
            except OSError:
                pass
        raise RuntimeError(
            f"빌드 중 피처 코드 변경 감지(code_sig {code_sig} → {now_sig}) — "
            f"{processed}/{total_pairs} 페어에서 조기 중단(혼합 패널 저장 금지). "
            f"피처 작업이 멈춘 뒤 다시 실행하라.")

    def _save_checkpoint(self, ck_rows, ck_meta, rows, done_keys, processed, total_pairs,
                         stock_codes, start_date, end_date, code_sig):
        """부분 진척을 원자적으로 저장한다(rows.pkl + meta.json). 실패해도 빌드는 계속한다.

        atomic: 임시파일 → os.replace. 재개 검증 키는 (유니버스, 구간, 피처코드 mtime)이다.
        """
        try:
            pd.DataFrame(rows).to_pickle(ck_rows + ".tmp")
            os.replace(ck_rows + ".tmp", ck_rows)
            meta = {"stock_codes": list(stock_codes), "start_date": start_date,
                    "end_date": end_date, "code_sig": code_sig, "processed": processed,
                    "total_pairs": total_pairs, "done_keys": sorted(done_keys),
                    "updated_at": datetime.now().isoformat(timespec="seconds")}
            with open(ck_meta + ".tmp", "w", encoding="utf-8") as f:
                json.dump(meta, f)
            os.replace(ck_meta + ".tmp", ck_meta)
            logger.info(f"체크포인트 저장: {processed}/{total_pairs} rows={len(rows)} → {ck_rows}")
        except Exception as e:
            logger.warning(f"체크포인트 저장 실패({type(e).__name__}: {e}) — 빌드는 계속")

    def _build_advanced_features(
        self, stock_code: str, date: str,
        market_df: pd.DataFrame = None,
    ) -> Dict:
        """Build 19 advanced features: sector/market, volatility/risk, flow, ownership, credit/margin, technical."""

        features = {}

        close = None
        volume = None
        if market_df is not None and not market_df.empty:
            close_series = market_df.get("close_price", market_df.get("close"))
            if close_series is not None:
                close = close_series.values if hasattr(close_series, "values") else np.array(close_series)

                close = np.array([float(c) for c in close])
            vol_series = market_df.get("volume")
            if vol_series is not None:
                volume = vol_series.values if hasattr(vol_series, "values") else np.array(vol_series)
                volume = np.array([float(v) for v in volume])

        valid_close = close is not None and len(close) > 0
        valid_vol = volume is not None and len(volume) > 0
        latest_close = float(close[-1]) if valid_close else 0.0





        features["sector_momentum"] = 0.0
        if self.pg_conn is not None and valid_close and len(close) >= 2:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("SELECT sector FROM stocks WHERE stock_code = %s", (stock_code,))
                row = cur.fetchone()
                if row and row[0]:
                    sector = row[0]
                    cur.execute("""
                        SELECT sp.close_price
                        FROM stock_prices sp
                        JOIN stocks s ON sp.stock_code = s.stock_code
                        WHERE s.sector = %s AND sp.trade_date = %s
                    """, (sector, date))
                    sector_rows = cur.fetchall()
                    if sector_rows:
                        sector_closes = [float(r[0]) for r in sector_rows if r[0]]
                        if len(sector_closes) >= 2:
                            stock_return = close[-1] / close[-2] - 1 if close[-2] != 0 else 0.0
                            sector_return = sum(sector_closes) / len(sector_closes) if sector_closes else 0.0
                            features["sector_momentum"] = float(sector_return)
                cur.close()
            except Exception as exc:
                _note_miss("sector_momentum", exc)
                self.pg_conn.rollback()


        features["relative_strength"] = 0.0
        if valid_close and len(close) >= 2:
            stock_ret = close[-1] / close[-2] - 1 if close[-2] != 0 else 0.0

            market_return = self._get_market_return(date)
            if market_return is not None:


                features["relative_strength"] = float(stock_ret - market_return)








        _mb_key = ("market_breadth", str(date))
        features["market_breadth"] = self._cache.get(_mb_key, 0.0)
        if _mb_key not in self._cache and self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT COUNT(*) FILTER (WHERE close_price > prev_close),
                           COUNT(*)
                    FROM (
                        SELECT trade_date, close_price,
                               LAG(close_price) OVER (
                                   PARTITION BY stock_code ORDER BY trade_date
                               ) AS prev_close
                        FROM stock_prices
                        WHERE trade_date <= %s AND trade_date >= %s::date - 14
                    ) t
                    WHERE trade_date = %s AND prev_close IS NOT NULL
                """, (date, date, date))
                row = cur.fetchone()
                if row and row[1] and row[1] > 0:
                    features["market_breadth"] = float(row[0] / row[1])
                cur.close()
            except Exception as exc:
                _note_miss("market_breadth", exc)
                self.pg_conn.rollback()
            self._cache[_mb_key] = features["market_breadth"]




        features["vix_proxy"] = 0.0
        if valid_close and len(close) >= 21:
            rets_20 = [(close[i] / close[i - 1] - 1) for i in range(max(1, len(close) - 20), len(close))]
            if rets_20:
                hist_vol = float(np.std(rets_20) * np.sqrt(252))
                features["vix_proxy"] = hist_vol


        features["volatility_skew"] = 0.0
        if valid_close and len(close) >= 21:
            rets_20 = [(close[i] / close[i - 1] - 1) for i in range(max(1, len(close) - 20), len(close))]
            if rets_20:
                upside = [r for r in rets_20 if r > 0]
                downside = [r for r in rets_20 if r < 0]
                up_vol = float(np.std(upside)) if len(upside) > 1 else 0.0
                dn_vol = float(np.std(downside)) if len(downside) > 1 else 0.0
                features["volatility_skew"] = up_vol - dn_vol










        features["program_trading_ratio"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT foreign_buy_value + foreign_sell_value, total_value
                    FROM krx_program_trading
                    WHERE trade_date <= %s AND market = 'KOSPI'
                    ORDER BY trade_date DESC LIMIT 1
                """, (date,))
                row = cur.fetchone()
                if row and row[1] and row[1] > 0:
                    features["program_trading_ratio"] = float(row[0] / row[1])
                cur.close()
            except Exception as exc:
                _note_miss("program_trading_ratio", exc)
                self.pg_conn.rollback()



        features["etf_flow_5d"] = 0.0





        features["foreign_ownership_pct"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT foreign_ownership_pct FROM ownership
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 1
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0]:
                    features["foreign_ownership_pct"] = float(row[0])
                cur.close()
            except Exception as exc:
                _note_miss("foreign_ownership_pct", exc)
                self.pg_conn.rollback()



        features["institution_ownership_pct"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT institution_ownership_pct FROM ownership
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 1
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0]:
                    features["institution_ownership_pct"] = float(row[0])
                cur.close()
            except Exception as exc:
                _note_miss("institution_ownership_pct", exc)
                self.pg_conn.rollback()



        features["retail_ownership_pct"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT 100.0 - COALESCE(foreign_ownership_pct, 0) - COALESCE(institution_ownership_pct, 0)
                    FROM ownership
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 1
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0]:
                    features["retail_ownership_pct"] = float(row[0])
                cur.close()
            except Exception as exc:
                _note_miss("retail_ownership_pct", exc)
                self.pg_conn.rollback()



        features["short_interest_ratio"] = 0.0
        features["days_to_cover"] = 0.0
        features["short_ratio_5d"] = 0.0
        features["short_ratio_trend"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT
                        sum(short_volume) FILTER (WHERE trade_date > %s::date - INTERVAL '28 days') AS sh20,
                        sum(total_volume) FILTER (WHERE trade_date > %s::date - INTERVAL '28 days') AS vol20,
                        avg(total_volume) FILTER (WHERE trade_date > %s::date - INTERVAL '28 days') AS avgvol20,
                        avg(short_ratio) FILTER (WHERE trade_date > %s::date - INTERVAL '7 days') AS sr5,
                        avg(short_ratio) AS sr20
                    FROM krx_short_selling
                    WHERE stock_code = %s AND trade_date <= %s::date
                """, (date, date, date, date, stock_code, date))
                row = cur.fetchone()
                cur.close()
                if row:
                    sh20, vol20, avgvol20, sr5, sr20 = [float(v) if v is not None else 0.0 for v in row]
                    if vol20 > 0:
                        features["short_interest_ratio"] = sh20 / vol20 * 100.0
                    if avgvol20 > 0:
                        features["days_to_cover"] = sh20 / (avgvol20 * 20.0)
                    features["short_ratio_5d"] = sr5
                    features["short_ratio_trend"] = sr5 - sr20
            except Exception as exc:
                _note_miss("group:short_selling", exc)
                self.pg_conn.rollback()

        features["margin_balance_change"] = 0.0



        features["credit_balance_change"] = 0.0



        features["short_selling_ratio"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT short_volume, total_volume FROM krx_short_selling
                    WHERE stock_code = %s AND trade_date = %s
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0] and row[1] and row[1] > 0:
                    features["short_selling_ratio"] = float(row[0] / row[1])
                cur.close()
            except Exception as exc:
                _note_miss("short_selling_ratio", exc)
                self.pg_conn.rollback()




        features["krx_total_trading_value"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT trading_value FROM krx_trading
                    WHERE trade_date = %s AND market = 'KOSPI' AND investor_type = 'Total'
                    LIMIT 1
                """, (date,))
                row = cur.fetchone()
                if row and row[0]:
                    features["krx_total_trading_value"] = float(row[0])
                cur.close()
            except Exception as exc:
                _note_miss("krx_total_trading_value", exc)
                self.pg_conn.rollback()




        _adr_key = ("krx_adr", str(date))
        features["krx_advance_decline_ratio"] = self._cache.get(_adr_key, 0.0)
        if _adr_key not in self._cache and self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    WITH r AS (
                        SELECT stock_code, trade_date, close_price,
                               LAG(close_price) OVER (
                                   PARTITION BY stock_code ORDER BY trade_date
                               ) AS prev_close
                        FROM stock_prices
                        WHERE trade_date <= %s AND trade_date >= %s::date - 14
                    ), d AS (
                        SELECT * FROM r WHERE trade_date = %s AND prev_close IS NOT NULL
                    )
                    SELECT COUNT(*) FILTER (WHERE close_price > prev_close),
                           COUNT(*) FILTER (WHERE close_price < prev_close)
                    FROM d
                """, (date, date, date))
                row = cur.fetchone()
                if row and row[1] and row[1] > 0:
                    features["krx_advance_decline_ratio"] = float(row[0] / row[1])
                cur.close()
            except Exception as exc:
                _note_miss("krx_advance_decline_ratio", exc)
                self.pg_conn.rollback()
            self._cache[_adr_key] = features["krx_advance_decline_ratio"]







        features["futures_premium"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT close_price FROM krx_derivatives
                    WHERE trade_date <= %s AND index_name = 'KOSPI200'
                    ORDER BY trade_date DESC LIMIT 1
                """, (date,))
                row = cur.fetchone()
                if row and row[0]:
                    features["futures_premium"] = float(row[0])
                cur.close()
            except Exception as exc:
                _note_miss("futures_premium", exc)
                self.pg_conn.rollback()




        features["derivatives_volume"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT SUM(volume) FROM krx_derivatives
                    WHERE trade_date = (
                        SELECT MAX(trade_date) FROM krx_derivatives WHERE trade_date <= %s
                    )
                """, (date,))
                row = cur.fetchone()
                if row and row[0]:
                    features["derivatives_volume"] = float(row[0])
                cur.close()
            except Exception as exc:
                _note_miss("derivatives_volume", exc)
                self.pg_conn.rollback()







        features["basis"] = 0.0
        features["basis_change_5d"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT basis FROM futures_options
                    WHERE trade_date <= %s AND basis IS NOT NULL
                    ORDER BY trade_date DESC LIMIT 6
                """, (date,))
                rows = cur.fetchall()
                cur.close()
                if rows:
                    latest = float(rows[0][0])
                    features["basis"] = latest
                    if len(rows) >= 6:
                        features["basis_change_5d"] = latest - float(rows[5][0])
            except Exception as exc:
                _note_miss("basis", exc)
            finally:




                try:
                    self.pg_conn.rollback()
                except Exception:
                    pass




        features["volatility_20d_rank"] = 0.0
        if valid_close and len(close) >= 60:
            all_rets = [(close[i] / close[i - 1] - 1) for i in range(1, len(close))]
            if len(all_rets) >= 60:
                current_vol = float(np.std(all_rets[-20:]))
                vols_60d = [float(np.std(all_rets[i-20:i])) for i in range(20, len(all_rets) + 1)]
                if vols_60d:
                    rank = sum(1 for v in vols_60d if v <= current_vol)
                    features["volatility_20d_rank"] = float(rank / len(vols_60d) * 100)


        features["volume_ratio_vs_avg"] = 0.0
        if valid_vol and len(volume) >= 20:
            current_vol_val = float(volume[-1])
            avg_vol_20 = float(np.mean(volume[-20:]))
            features["volume_ratio_vs_avg"] = float(current_vol_val / avg_vol_20) if avg_vol_20 > 0 else 0.0


        features["price_vs_sector"] = 0.0
        if valid_close and len(close) >= 2:
            stock_ret_1d = close[-1] / close[-2] - 1 if close[-2] != 0 else 0.0

            features["price_vs_sector"] = float(stock_ret_1d - features.get("sector_momentum", 0.0))


        features["beta_60d"] = 0.0

        if valid_close and len(close) >= 61:
            stock_rets = [(close[i] / close[i - 1] - 1) for i in range(max(1, len(close) - 60), len(close))]
            if len(stock_rets) >= 2:
                features["beta_60d"] = float(np.std(stock_rets) * np.sqrt(252))


        features["momentum_divergence"] = 0.0
        if valid_close and len(close) >= 14:
            gains, losses = [], []
            for i in range(1, len(close)):
                change = close[i] - close[i - 1]
                gains.append(max(change, 0))
                losses.append(max(-change, 0))
            if gains and losses:
                avg_gain = float(np.mean(gains[-14:]))
                avg_loss = float(np.mean(losses[-14:]))
                if avg_loss != 0:
                    rsi = 100 - (100 / (1 + avg_gain / avg_loss))
                    recent_rets = [close[i] / close[i - 1] - 1 for i in range(max(1, len(close) - 5), len(close))]
                    price_trend = float(np.mean(recent_rets)) if recent_rets else 0.0
                    rsi_mid = rsi - 50
                    features["momentum_divergence"] = float(price_trend * rsi_mid)

        return features

    def _get_stock_sentiment(self, stock_code: str, date: str) -> Dict:
        """SNS 원천(sns_post_features) 우선, 없으면 stock_sentiment 로 폴백.

        WHY(2026-11-24): stock_sentiment 는 8일치(252종목)뿐이라 과거 패널의 감성
        피처가 전부 0 이었다. sns_post_features 는 495종목·244일치가 있어 그쪽을
        원천으로 쓴다. 둘 다 없으면 0.0 이 아니라 결측으로 집계된다(_note_miss).
        """
        result = {
            "sentiment_avg_db": 0.0,
            "sentiment_momentum": 0.0,
        }
        if self.pg_conn is None:
            return result
        try:
            cur = self.pg_conn.cursor()
            cur.execute("""
                SELECT
                    avg(sentiment_score) FILTER (WHERE trade_date = %s::date) AS today_score,
                    avg(sentiment_score) FILTER (WHERE trade_date < %s::date) AS prev_score,
                    sum(post_count) FILTER (WHERE trade_date = %s::date) AS posts
                FROM sns_post_features
                WHERE stock_code = %s
                  AND trade_date <= %s::date
                  AND trade_date > (%s::date - INTERVAL '10 days')
            """, (date, date, date, stock_code, date, date))
            row = cur.fetchone()
            cur.close()
            if row and row[0] is not None:
                result["sentiment_avg_db"] = float(row[0])
                if row[1] is not None:
                    result["sentiment_momentum"] = float(row[0]) - float(row[1])
                return result
        except Exception as exc:
            _note_miss("group:sentiment_sns", exc)
            self.pg_conn.rollback()
        try:
            cur = self.pg_conn.cursor()
            cur.execute("""
                SELECT avg_sentiment, positive_count, negative_count, sentiment_count
                FROM stock_sentiment
                WHERE stock_code = %s AND analysis_date = %s
                LIMIT 1
            """, (stock_code, date))
            row = cur.fetchone()
            if row and row[0] is not None:
                result["sentiment_avg_db"] = float(row[0])

                total = (row[1] or 0) + (row[2] or 0)
                if total > 0:
                    result["sentiment_momentum"] = float((row[1] - row[2]) / total)
            cur.close()
        except Exception as exc:
            _note_miss("_get_stock_sentiment", exc)
            self.pg_conn.rollback()
        return result

    def _get_economic_events(self, date: str) -> Dict:
        """Query economic_events table for event count and impact in past 7 days."""
        result = {
            "economic_event_count_7d": 0.0,
            "economic_event_impact": 0.0,
        }
        if self.pg_conn is None:
            return result
        try:
            cur = self.pg_conn.cursor()
            past_7d = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
            cur.execute("""
                SELECT COUNT(*), SUM(CASE WHEN importance = 'high' THEN 3 WHEN importance = 'medium' THEN 1 ELSE 0 END)
                FROM economic_events
                WHERE event_date >= %s AND event_date <= %s
            """, (past_7d, date))
            row = cur.fetchone()
            if row:
                result["economic_event_count_7d"] = float(row[0] or 0)
                result["economic_event_impact"] = float(row[1] or 0)
            cur.close()
        except Exception as exc:
            _note_miss("_get_economic_events", exc)
            self.pg_conn.rollback()
        return result

    @staticmethod
    def _connect_neo4j():
        """env(NEO4J_URI/USER/PASSWORD)로 Neo4j 드라이버를 만든다. 실패 시 None."""
        try:
            from neo4j import GraphDatabase

            from app.config import Config

            uri = getattr(Config, "NEO4J_URI", None)
            user = getattr(Config, "NEO4J_USER", None)
            pwd = getattr(Config, "NEO4J_PASSWORD", None)
            if not uri or not pwd:
                return None
            drv = GraphDatabase.driver(uri, auth=(user, pwd))
            drv.verify_connectivity()
            logger.info("Neo4j 연결 성공: %s (그래프 피처 활성)", uri)
            return drv
        except Exception as e:
            logger.debug(f"Neo4j 연결 실패, 그래프 피처는 0.0 유지: {e}")
            return None

    def _get_market_return(self, date: str):
        """해당 날짜의 전종목 동일가중 평균 1일 수익률 (날짜별 1회 캐시).

        지수 테이블이 없으므로 market_data 기반 프록시를 쓴다. 10일 창 안에서만
        LAG 를 계산한 뒤 마지막 행(당일)들의 평균을 낸다.
        """
        key = ("market_return", str(date))
        if key in self._cache:
            return self._cache[key]
        if self.pg_conn is None:
            return None
        val = None
        try:
            cur = self.pg_conn.cursor()
            cur.execute(f"""
                SELECT AVG(r) FROM (
                    SELECT (close_price / NULLIF(LAG(close_price) OVER (
                                PARTITION BY stock_code ORDER BY trade_date), 0)) - 1 AS r,
                           trade_date
                    FROM market_data
                    WHERE trade_date BETWEEN %s::date - INTERVAL '10 days' AND %s::date
                      AND {MARKET_DATA_VALID}
                ) t
                WHERE trade_date = %s::date AND r IS NOT NULL
            """, (date, date, date))
            row = cur.fetchone()
            cur.close()
            if row and row[0] is not None:
                val = float(row[0])
        except Exception:
            logger.debug("market_return unavailable; relative_strength stays 0.0")
            try:
                self.pg_conn.rollback()
            except Exception:
                pass
        self._cache[key] = val
        return val

    def get_feature_names(self) -> List[str]:
        """Return the list of all expected feature names (for model training consistency)."""
        return sorted([
            "price", "return_1d", "return_5d", "return_20d",
            "volatility_20d", "volatility_60d",
            "ma_position_5", "ma_position_20", "ma_position_60", "ma_position_120",
            "rsi", "macd", "bb_width", "bb_position", "atr", "atr_pct",
            "stoch_k", "stoch_d",
            "volume_ratio_5", "volume_ratio_20",
            "foreign_net_buy", "foreign_net_buy_5d",
            "institution_net_buy", "institution_net_buy_5d",
            "basis", "basis_change_5d",
            "revenue", "operating_profit", "net_income",
            "op_margin", "net_margin",
            "per_current", "pbr_current", "roe", "debt_ratio",
            "revenue_growth_yoy", "op_margin_change_yoy",
            "per_percentile", "pbr_percentile",
            "sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d",
            "sentiment_trend", "sentiment_volatility",
            "news_count_5d", "news_count_20d", "disclosure_count_5d",
            "authenticity_avg", "positive_ratio", "negative_ratio",
            "interest_rate", "interest_rate_change_1m", "interest_rate_change_3m",
            "fx_usd_krw", "fx_change_1m", "fx_change_3m",
            "oil_wti", "oil_change_1m", "oil_change_3m",
            "cpi_yoy", "ppi_yoy", "yield_spread", "credit_spread",
            "sector_count", "theme_count", "theme_max_relevance",
            "twin_count", "twin_avg_correlation",
            "cycle_up", "cycle_down",
            "avg_similarity_top10", "max_similarity", "similarity_std",
            "similar_count", "similar_stocks_return_avg", "similar_stocks_return_std",


            "value_per", "value_pbr", "value_psr", "value_pcr", "value_ncav",
            "value_ev_ebit", "value_pfcr",
            "quality_cp_to_assets", "quality_op_to_equity", "quality_roe",
            "quality_roa", "quality_f_score", "quality_asset_growth",
            "quality_debt_ratio_change", "quality_op_growth",
            "quality_earnings_volatility", "quality_price_volatility_60d",
            "quality_beta",
            "momentum_1m_reverse", "momentum_3_12m", "momentum_op", "momentum_ni",

            "sector_momentum", "relative_strength",
            "vix_proxy", "volatility_skew",
            "program_trading_ratio", "etf_flow_5d",
            "foreign_ownership_pct", "institution_ownership_pct", "retail_ownership_pct",
            "short_interest_ratio", "days_to_cover",
            "short_ratio_5d", "short_ratio_trend",
            "margin_balance_change", "credit_balance_change", "short_selling_ratio",
            "volatility_20d_rank", "volume_ratio_vs_avg", "price_vs_sector",
            "beta_60d", "momentum_divergence",


            "krx_total_trading_value", "krx_advance_decline_ratio",


            "futures_premium", "derivatives_volume",


            "sentiment_avg_db", "sentiment_momentum",


            "economic_event_count_7d", "economic_event_impact",


            "momentum_vs_volatility", "trend_interaction", "volume_price_trend",
            "cross_trend", "volatility_volume", "short_medium_term_momentum",
            "trend_confirmation", "price_volume",


            "return_5d_mean_10d", "volatility_20d_mean_10d", "volume_ratio_5_mean_10d",


            "rank_return_5d", "rank_return_20d", "rank_volatility_20d",
            "rank_volume_ratio_5", "rank_ma_position_5", "rank_volume_ratio_20",


            "target_ma_5", "target_ma_10", "target_ma_20",


            "kalman_momentum_1d", "kalman_momentum_5d", "kalman_volatility",


            "bayes_momentum_1d", "bayes_momentum_5d",
            "bayes_volatility", "bayes_gain_uncertainty",


            "quality_score",


            *sns_feature_names(),


            "market_impact_score",
            "event_realized_5d", "event_mna_5d", "event_capital_increase_5d",
            "event_cb_bw_5d", "event_stake_change_5d", "event_contract_5d",
            "event_new_product_5d", "event_patent_5d", "event_regulation_5d",
            "event_litigation_5d", "event_delisting_5d", "event_recall_5d",
            "event_treasury_5d", "event_exec_change_5d", "event_partnership_5d",
            "event_macro_5d", "event_market_liquidity_5d", "event_disaster_5d",
            "event_shareholder_meeting_5d", "event_ir_5d", "event_dividend_5d",
            "event_debt_guarantee_5d", "event_option_grant_5d", "event_investment_5d",
            "event_rumor_5d",
            "theme_exposure_5d",



            *(intraday_feature_names() if self.use_intraday else []),


            *(us_market_feature_names() if self.use_us_market else []),
        ])

    def set_db_connections(self, pg_conn=None, neo4j_conn=None):
        """Set or update database connections."""
        self.pg_conn = pg_conn
        self.neo4j_conn = neo4j_conn

    def clear_cache(self):
        """Clear the feature cache."""
        self._cache.clear()
