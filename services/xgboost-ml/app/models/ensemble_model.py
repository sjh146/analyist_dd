import json
import logging
import os
import numpy as np
from typing import Dict, List, Optional

from .xgboost_model import XGBoostModel
from .lightgbm_model import LightGBMModel
from .catboost_model import CatBoostModel

logger = logging.getLogger(__name__)

# val-AUC 가중치를 모델 디렉터리에 저장하는 파일명.
WEIGHTS_FILENAME = "ensemble_weights.json"
# 저장된 가중치를 **추론에 사용할지** 결정하는 env 플래그. 기본 OFF = 균등 평균(종전 배포 동작과
# 비트 동일). 실측(2026-10-02 MT116): 학습 경로는 meta.ensemble_auc·auc.txt 를 val-AUC **가중**
# 평균으로 보고하지만, load() 는 가중치를 복원하지 않아 배포 추론은 **균등** 평균이었다 →
# 보고 지표(승격 게이트 입력)와 실제 배포 점수가 다른 함수였다. 이 플래그를 켜면 그 간극이 닫힌다.
ENV_USE_STORED_WEIGHTS = "ENSEMBLE_USE_STORED_WEIGHTS"


def _use_stored_weights() -> bool:
    return os.environ.get(ENV_USE_STORED_WEIGHTS, "").strip().lower() in ("1", "true", "yes", "on")


class EnsembleModel:
    """
    Soft Voting Ensemble combining XGBoost, LightGBM, and CatBoost.
    Predicts by weighted averaging probability outputs from all models,
    where weights are derived from validation AUC scores.
    """

    def __init__(self, model_dir: str = "models"):
        self.models = []
        self.model_names = []

        # XGBoost
        self.models.append(XGBoostModel())
        self.model_names.append('xgboost')

        # LightGBM
        self.models.append(LightGBMModel(model_dir))
        self.model_names.append('lightgbm')

        # CatBoost (with graceful fallback)
        try:
            self.models.append(CatBoostModel())
            self.model_names.append('catboost')
        except ImportError:
            logger.warning("CatBoost not installed — skipping CatBoost in ensemble")

        self._is_trained = False
        self.val_weights: Dict[str, float] = {}

    def train(self, X_train, y_train, X_val=None, y_val=None, feature_names=None,
              sample_weight=None):
        """sample_weight: 행별 학습 가중치(선택). None(기본) 이면 기존과 완전히 동일하게
        균등 가중으로 학습한다(프로덕션 경로 무변경 — 스윕 실험 전용 확장)."""
        metrics = {}
        self.val_weights = {}

        for name, model in zip(self.model_names, self.models):
            logger.info(f"Training {name}...")
            try:
                m = model.train(X_train, y_train, X_val, y_val,
                                sample_weight=sample_weight)
                if m:
                    for k, v in m.items():
                        metrics[f"{name}_{k}"] = v
            except ImportError as e:
                logger.warning(f"Skipping {name} — not available: {e}")
                continue
            except Exception as e:
                logger.warning(f"Skipping {name} — train failed: {e}")
                continue

            # Compute validation AUC for weighting
            if X_val is not None and y_val is not None:
                from sklearn.metrics import roc_auc_score
                try:
                    val_probs = model.predict(X_val)
                    auc = roc_auc_score(y_val, val_probs)
                except Exception:
                    auc = 0.5
                # Weight = AUC above random (0.5), minimum 0.01
                self.val_weights[name] = max(auc - 0.5, 0.01)
                logger.info(f"  {name} val AUC: {auc:.4f} (weight: {self.val_weights[name]:.4f})")
            else:
                self.val_weights[name] = 1.0

        self._is_trained = True
        return metrics

    def predict(self, X) -> np.ndarray:
        if not self.models:
            logger.error("No models loaded for prediction!")
            return np.zeros(len(X))

        # Weighted average based on validation AUC
        preds = []
        total_weight = 0.0
        for name, model in zip(self.model_names, self.models):
            probs = model.predict(X)
            weight = self.val_weights.get(name, 1.0)
            preds.append(probs * weight)
            total_weight += weight

        if total_weight > 0:
            ensemble_pred = np.sum(preds, axis=0) / total_weight
        else:
            ensemble_pred = np.mean(preds, axis=0) if preds else np.zeros(len(X))

        return ensemble_pred

    def predict_single(self, features: np.ndarray) -> dict:
        results = {}
        model_probs = []
        total_weight = 0.0
        weighted_sum = 0.0

        for name, model in zip(self.model_names, self.models):
            try:
                result = model.predict_single(features)
                results[name] = result
                prob = result['predicted_probability']
                weight = self.val_weights.get(name, 1.0)
                model_probs.append(prob)
                weighted_sum += prob * weight
                total_weight += weight
            except Exception as e:
                logger.warning(f"{name} prediction failed: {e}")
                continue

        if not model_probs:
            raise ValueError("All models failed to predict")

        if total_weight > 0:
            avg_prob = weighted_sum / total_weight
        else:
            avg_prob = np.mean(model_probs)

        direction = "up" if avg_prob >= 0.5 else "down"
        confidence = max(avg_prob, 1 - avg_prob)

        return {
            "ensemble": {
                "direction": direction,
                "confidence": float(confidence),
                "probability": float(avg_prob),
            },
            "models": results,
            "model_count": len(model_probs),
        }

    def save(self, path: str = None) -> list:
        if path is None:
            path = "models"
        paths = []
        for name, model in zip(self.model_names, self.models):
            model_path = f"{path}/{name}_model.pkl"
            model.save(model_path)
            paths.append(model_path)
        # val-AUC 가중치를 함께 저장한다(부산물 — 추론 동작은 ENV 플래그가 켜지기 전까지 불변).
        self.save_weights(path)
        return paths

    def save_weights(self, path: Optional[str] = None) -> Optional[str]:
        """val-AUC 가중치를 ``{path}/ensemble_weights.json`` 으로 저장한다.

        가중치가 없으면(학습 전) 아무것도 쓰지 않는다. 저장 자체는 예측을 바꾸지 않는다 —
        사용 여부는 ``load_weights`` 의 ENV 플래그가 결정한다.
        """
        if path is None:
            path = "models"
        if not self.val_weights:
            return None
        fp = os.path.join(path, WEIGHTS_FILENAME)
        try:
            os.makedirs(path, exist_ok=True)
            with open(fp, "w") as f:
                json.dump({k: float(v) for k, v in self.val_weights.items()}, f, indent=2)
            logger.info("Saved ensemble weights to %s", fp)
            return fp
        except Exception as e:  # 저장 실패가 학습을 막지 않는다
            logger.warning("Failed to save ensemble weights to %s: %s", fp, e)
            return None

    def load_weights(self, path: Optional[str] = None) -> dict:
        """저장된 가중치를 읽어 (플래그가 켜져 있으면) 채택한다.

        기본(플래그 OFF)은 **빈 dict** 를 돌려준다 → ``predict``/``predict_single`` 의
        ``weight = val_weights.get(name, 1.0)`` 이 전부 1.0 이 되어 **균등 평균**(종전 배포
        동작과 비트 동일)이 된다. ``ENSEMBLE_USE_STORED_WEIGHTS=1`` 일 때만 파일 내용을 쓴다.
        파일이 없거나 깨졌으면 조용히 균등 경로로 폴백하고 로그를 남긴다.
        """
        if path is None:
            path = "models"
        fp = os.path.join(path, WEIGHTS_FILENAME)
        if not os.path.exists(fp):
            return {}
        try:
            with open(fp, "r") as f:
                raw = json.load(f)
            weights = {str(k): float(v) for k, v in (raw or {}).items()}
        except Exception as e:
            logger.warning("Failed to read ensemble weights %s (%s) — 균등 평균으로 폴백", fp, e)
            return {}
        if not weights:
            return {}
        if not _use_stored_weights():
            logger.info(
                "ensemble weights found at %s but %s is off — 균등 평균(배포 동작) 사용",
                fp, ENV_USE_STORED_WEIGHTS,
            )
            return {}
        self.val_weights = weights
        logger.info("Using stored ensemble weights: %s", weights)
        return weights

    def save_feature_names(self, feature_names: list, path: str = None):
        if path is None:
            path = "models"
        os.makedirs(path, exist_ok=True)
        fp = os.path.join(path, "feature_names.json")
        with open(fp, "w") as f:
            json.dump(feature_names, f)
        logger.info(f"Saved {len(feature_names)} feature names to {fp}")

    def load_feature_names(self, path: str = None) -> list:
        if path is None:
            path = "models"
        fp = os.path.join(path, "feature_names.json")
        if not os.path.exists(fp):
            logger.warning(f"No feature_names.json found at {fp}")
            return []
        with open(fp, "r") as f:
            names = json.load(f)
        logger.info(f"Loaded {len(names)} feature names from {fp}")
        return names

    def load(self, path: str = None):
        if path is None:
            path = "models"
        loaded = 0
        for name, model in zip(self.model_names, self.models):
            try:
                model_path = f"{path}/{name}_model.pkl"
                model.load(model_path)
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load {name} model: {e}")
        if loaded > 0:
            self._is_trained = True
        # 저장된 val-AUC 가중치 복원(기본 OFF = 균등 평균 — 종전 배포 동작과 동일).
        self.load_weights(path)

    def feature_importance(self) -> dict:
        all_importances = {}
        counts = {}
        for name, model in zip(self.model_names, self.models):
            try:
                imp = model.feature_importance()
                for feat, score in imp.items():
                    all_importances[feat] = all_importances.get(feat, 0) + score
                    counts[feat] = counts.get(feat, 0) + 1
            except Exception:
                continue

        return {feat: score / counts[feat] for feat, score in all_importances.items()}

    def get_params(self) -> dict:
        params = {}
        for name, model in zip(self.model_names, self.models):
            try:
                if hasattr(model, 'get_params'):
                    params[name] = model.get_params()
                else:
                    params[name] = {}
            except Exception as e:
                logger.warning(f"Failed to get params for {name}: {e}")
                params[name] = {}
        return params
