"""
Predictor
Runs daily inference and publishes high-confidence signals to Redis.
"""

import json
import logging
import os
import hmac
import hashlib
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# --- 배포 추론 유니버스 필터 (측정정합성, 2026-10-10 CG159) --------------------
# WHY: 배포 추론은 `stocks` **전 종목**(실측 4,343)을 순회하는데 학습 유니버스
# (`select_training_universe`)는 ETF/ETN 을 제외한다. 피처가 없는 종목은 올제로 벡터 →
# 모델이 **같은 확률 상수**를 돌려주고 그 값이 하루 종일 동일하다(실측 2026-10-10:
# 상수 블록 373행 중 369행이 ETN, 값 0.5689 = 소비 문턱 0.55 초과 → 그날 ≥0.55 대역의 56.8%).
# 상수 행은 서로 순위를 정할 수 없고, 값이 문턱을 넘으면 **정보 없는 행이 후보**가 된다.
# 기본값은 **현행 유지(OFF)** — 켜면 학습 유니버스와 같은 ETF/ETN 제외 규칙을 적용한다.
# 발행 리스트가 바뀌는 변경이므로 **활성화는 리뷰보드 승인 대상**(감사:
# scripts/prediction_constant_block_audit.py · 증거 data/reports/prediction_constant_block_audit_20261010.json).
PREDICT_EXCLUDE_ETFETN_ENV = "PREDICT_EXCLUDE_ETFETN"
_TRUTHY_VALUES = ("1", "true", "yes", "on", "y")


def prediction_universe_excludes_etf_etn() -> bool:
    """플래그가 켜졌는가. 미설정/거짓값이면 False = 종전과 **비트 동일**(무회귀)."""
    return (os.environ.get(PREDICT_EXCLUDE_ETFETN_ENV) or "").strip().lower() in _TRUTHY_VALUES


def filter_prediction_universe(stocks: List[Dict]) -> List[Dict]:
    """배포 추론에 쓸 종목 목록. 플래그 OFF 면 **입력 그대로**(객체 동일) 돌려준다.

    ON 이면 학습 유니버스와 같은 규칙(ETF/ETN 이름 패턴 제외 — `app.training.universe.is_etf_etn`)을
    적용하되 **순서는 보존**한다(제거 외의 변경 금지). 자체점검: scripts/_predict_universe_filter_test.py.
    """
    if not prediction_universe_excludes_etf_etn():
        return stocks
    from app.training.universe import is_etf_etn  # 사용 시점 임포트(순환 임포트 방지)

    kept: List[Dict] = []
    dropped = 0
    for s in stocks:
        name = s.get("stock_name") if hasattr(s, "get") else None
        if is_etf_etn(name):
            dropped += 1
            continue
        kept.append(s)
    logger.info(
        "predict universe filter ON(%s): %d -> %d (ETF/ETN %d 제외)",
        PREDICT_EXCLUDE_ETFETN_ENV, len(stocks), len(kept), dropped,
    )
    return kept


try:
    import redis
except ImportError:
    redis = None

try:
    from services.shared.redis_streams import RedisStreams
except ImportError:
    RedisStreams = None


# --- 배포 모델 식별자(model_version) -----------------------------------------
# WHY: ml_predictions.model_version 이 항상 'v1.0'(ML_MODEL_VERSION 기본값)이라 승격/롤백
# 전후 예측을 구분할 수 없었다 → 전방 성적표(scripts/forward_scorecard.py, CG68)가
# "이 모델의 전방 성적"을 낼 수 없다. 배포 챔피언의 메타에서 식별자를 만들어
# 예측·시그널에 귀속시킨다(스키마·소비자 계약 변경 없음 — 기존 컬럼의 값만 바뀐다).
_LEGACY_MODEL_VERSION = "v1.0"
_MODEL_VERSION_CACHE: Optional[str] = None


def _resolve_model_version(models_dir: Optional[str] = None) -> str:
    """배포 모델 식별자. 프로세스당 1회 계산해 캐시한다(모델 재로드 시 프로세스가 다시 뜬다).

    우선순위:
      1) ML_MODEL_VERSION 이 'v1.0'(레거시 자리표시자)이 아니면 그대로 존중.
      2) <models>/champion/robust_auc.json → 'champ-<recorded_at>-<auc>'.
      3) <models>/champion/auc.txt (mtime+값) → 'champ-<mtime>-<auc>'.
      4) 최후 'v1.0'.
    """
    global _MODEL_VERSION_CACHE
    if _MODEL_VERSION_CACHE is not None:
        return _MODEL_VERSION_CACHE

    env = (os.environ.get("ML_MODEL_VERSION") or "").strip()
    if env and env != _LEGACY_MODEL_VERSION:
        _MODEL_VERSION_CACHE = env
        return env

    base = models_dir or os.path.join(os.path.dirname(__file__), "..", "models")
    champ = os.path.join(base, "champion")
    try:
        meta = os.path.join(champ, "robust_auc.json")
        if os.path.exists(meta):
            with open(meta) as f:
                d = json.load(f)
            ts = str(d.get("recorded_at") or "")
            ts = ts.replace("-", "").replace(":", "")
            auc = d.get("robust_auc")
            if ts and auc is not None:
                _MODEL_VERSION_CACHE = f"champ-{ts}-{float(auc):.4f}"
                return _MODEL_VERSION_CACHE
    except Exception as e:  # noqa: BLE001
        logger.warning(f"model_version: robust_auc.json 읽기 실패: {e}")
    try:
        auc_txt = os.path.join(champ, "auc.txt")
        if os.path.exists(auc_txt):
            with open(auc_txt) as f:
                auc = float(f.read().strip())
            mt = datetime.fromtimestamp(os.path.getmtime(auc_txt)).strftime("%Y%m%dT%H%M%S")
            _MODEL_VERSION_CACHE = f"champ-{mt}-{auc:.4f}"
            return _MODEL_VERSION_CACHE
    except Exception as e:  # noqa: BLE001
        logger.warning(f"model_version: auc.txt 읽기 실패: {e}")

    _MODEL_VERSION_CACHE = _LEGACY_MODEL_VERSION
    return _MODEL_VERSION_CACHE


def _sign_signal(data: dict) -> dict:
    """TRADE_SIGNAL_SECRET로 HMAC-SHA256 서명 추가 (CWE-306 — 무인증 신호 주입 차단).

    trade-executor의 verify_signal_signature와 동일한 canonical 방식:
    정렬된 key=value 를 '&'로 결합해 HMAC. 시크릿 미설정 시 서명 없이 반환
    (소비자 측 fail-closed이므로 운영은 반드시 설정).
    """
    secret = os.environ.get("TRADE_SIGNAL_SECRET", "")
    if not secret:
        logger.warning("TRADE_SIGNAL_SECRET 미설정 — 서명 없이 발행 (소비자가 거부할 수 있음)")
        return data
    canonical = "&".join(f"{k}={v}" for k, v in sorted(data.items()))
    sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    out = dict(data)
    out["sig"] = sig
    return out


class Predictor:
    """Runs predictions for all tracked stocks and publishes signals."""

    def __init__(self, storage, feature_pipeline, model, redis_client=None):
        self.storage = storage
        self.feature_pipeline = feature_pipeline
        self.model = model
        self.redis_client = redis_client or self._create_redis_client()
        self._streams = None
        if RedisStreams:
            try:
                host = os.environ.get("REDIS_HOST", "redis")
                port = int(os.environ.get("REDIS_PORT", 6379))
                self._streams = RedisStreams(f"redis://{host}:{port}")
            except Exception:
                pass
        # Load saved model feature names for inference (model was trained with these)
        self._saved_feature_names = self._load_saved_feature_names()

    def _load_saved_feature_names(self) -> List[str]:
        """Load feature names from the saved model's feature_names.json."""
        try:
            path = os.path.join(
                os.path.dirname(__file__), "..", "models", "champion", "feature_names.json"
            )
            if os.path.exists(path):
                with open(path) as f:
                    names = json.load(f)
                logger.info(f"Loaded {len(names)} saved feature names for inference")
                return names
        except Exception as e:
            logger.warning(f"Could not load saved feature names: {e}")
        return self.feature_pipeline.get_feature_names()

    def predict(self, stock_code: str, date: str = None) -> Optional[Dict]:
        """Predict direction for a single stock."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        try:
            features = self.feature_pipeline.build_features(stock_code, date)
            feature_names = self._saved_feature_names
            feature_vector = np.array([
                features.get(f, 0.0) for f in feature_names
            ], dtype=np.float32)

            if np.isnan(feature_vector).any():
                feature_vector = np.nan_to_num(feature_vector, nan=0.0)

            result = self.model.predict_single(feature_vector)

            return {
                "stock_code": stock_code,
                "prediction_date": date,
                "model_version": _resolve_model_version(),
                "direction": result["predicted_direction"],
                "confidence": float(result["confidence"]),
                "probability": float(result["predicted_probability"]),
            }

        except Exception as e:
            logger.debug(f"Prediction failed for {stock_code}: {e}")
            return None

    def predict_all(self) -> List[Dict]:
        """Run predictions for all tracked stocks."""
        stocks = filter_prediction_universe(self.storage.get_all_stocks())
        predictions = []

        for stock in stocks:
            pred = self.predict(stock["stock_code"])
            if pred:
                predictions.append(pred)

        logger.info(f"Generated {len(predictions)} predictions")
        return predictions

    def _get_signal_stream_name(self) -> str:
        return os.environ.get("REDIS_SIGNAL_STREAM", "trading:signals")

    def publish_signals_to_redis(self, predictions: List[Dict]):
        """Publish top predictions to Redis Streams."""
        if not self._streams:
            logger.warning("Redis Streams not available; skipping signal publish")
            return

        filtered = [
            p for p in predictions
            if p["confidence"] >= 0.6 and p["direction"] in ("up", "down")
        ]
        top = sorted(filtered, key=lambda x: x["confidence"], reverse=True)[:10]

        stream_name = self._get_signal_stream_name()

        for pred in top:
            try:
                direction = pred["direction"]
                timestamp = datetime.now().isoformat()

                signal_data = {
                    "stock_code": pred["stock_code"],
                    "signal": "buy" if direction == "up" else "sell",
                    "confidence": pred["confidence"],
                    "timestamp": timestamp,
                    "model_version": _resolve_model_version(),
                }
                signal_data = _sign_signal(signal_data)

                self._streams.xadd(stream_name, signal_data, maxlen=10000)
                from app.metrics_integration import on_redis_publish, on_signal_generated

                on_redis_publish(stream_name)
                on_signal_generated()
                logger.info(
                    f"Signal streamed: {pred['stock_code']} "
                    f"{direction} ({pred['confidence']:.2f})"
                )
            except Exception as e:
                logger.error(f"Redis Streams xadd failed for {pred['stock_code']}: {e}")

        logger.info(f"Published {len(top)} signals to Redis Streams")

    def _create_redis_client(self):
        """Create Redis client from environment config."""
        if not redis:
            return None
        try:
            host = os.environ.get("REDIS_HOST", "redis")
            port = int(os.environ.get("REDIS_PORT", 6379))
            password = os.environ.get("REDIS_PASSWORD", "")
            return redis.Redis(
                host=host, port=port, password=password,
                decode_responses=True, socket_connect_timeout=5,
            )
        except Exception:
            return None
