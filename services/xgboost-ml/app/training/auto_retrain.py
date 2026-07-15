"""
AutoRetrainer
Orchestrates the full auto-retraining loop: data prep, challenger training,
ensemble training, evaluation, champion selection, and deployment.
"""

import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score, roc_auc_score

from app.config import Config
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.feature_engine.feature_store import FeatureStore
from app.models.xgboost_model import XGBoostModel
from app.models.lightgbm_model import LightGBMModel
from app.models.catboost_model import CatBoostModel
from app.models.ensemble_model import EnsembleModel
from app.storage.postgres_storage import PostgresStorage
from app.training.trainer import Trainer

logger = logging.getLogger(__name__)

CHALLENGER_DIR = "models/challenger"
CHAMPION_DIR = "models/champion"
METRICS_DIR = "ml_metrics"


class AutoRetrainer:
    """End-to-end auto-retraining pipeline orchestrator."""

    def __init__(self, storage: Optional[PostgresStorage] = None,
                 feature_pipeline: Optional[FeaturePipeline] = None):
        self.config = Config()
        self.storage = storage or PostgresStorage()
        self.feature_pipeline = feature_pipeline or FeaturePipeline(
            use_feature_store=True,
            feature_store=FeatureStore(),
        )
        self.trainer = Trainer(self.storage, self.feature_pipeline)
        os.makedirs(CHALLENGER_DIR, exist_ok=True)
        os.makedirs(CHAMPION_DIR, exist_ok=True)
        os.makedirs(METRICS_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Data Collection Check
    # ------------------------------------------------------------------
    def check_data_collection(self) -> Dict:
        """Query feature_store.feature_values for recent row count."""
        result = {
            "status": "ok",
            "recent_row_count": 0,
            "days_covered": 0,
            "message": "",
        }
        try:
            conn = self.storage._get_conn()
            if not conn:
                result["status"] = "error"
                result["message"] = "No database connection available"
                return result

            cur = conn.cursor()
            # Count rows in the last 7 days
            cur.execute("""
                SELECT COUNT(*) AS recent_rows,
                       COUNT(DISTINCT date) AS days_covered
                FROM feature_store.feature_values
                WHERE date >= CURRENT_DATE - INTERVAL '7 days'
            """)
            row = cur.fetchone()
            cur.close()
            self.storage._put_conn(conn)

            if row:
                result["recent_row_count"] = row[0] or 0
                result["days_covered"] = row[1] or 0

            if result["recent_row_count"] == 0:
                result["status"] = "warning"
                result["message"] = "No recent feature values found in feature_store"
            else:
                result["message"] = (
                    f"Found {result['recent_row_count']} feature rows "
                    f"across {result['days_covered']} days"
                )

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
            logger.error(f"Data collection check failed: {e}")

        return result

    # ------------------------------------------------------------------
    # 2. Feature Generation
    # ------------------------------------------------------------------
    def generate_features(self, days: int = 365) -> Dict:
        """Generate training features for the last N days."""
        result = {
            "status": "ok",
            "feature_count": 0,
            "stock_count": 0,
            "date_range": "",
            "message": "",
        }
        try:
            stocks = self.storage.get_all_stocks()
            stock_codes = [s["stock_code"] for s in stocks] if stocks else ["005930"]

            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            date_range_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
            result["date_range"] = date_range_str

            df = self.feature_pipeline.build_training_features(
                stock_codes,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
            )

            if df is None or df.empty:
                result["status"] = "error"
                result["message"] = "Feature generation returned empty DataFrame"
                return result

            result["feature_count"] = len(df)
            result["stock_count"] = len(stock_codes)
            result["message"] = (
                f"Generated {len(df)} feature rows for {len(stock_codes)} stocks "
                f"from {date_range_str}"
            )

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
            logger.error(f"Feature generation failed: {e}")

        return result

    # ------------------------------------------------------------------
    # 3. Prepare Training Data (shared by challenger training)
    # ------------------------------------------------------------------
    def prepare_data(self, days: int = 365) -> Dict:
        """Prepare training/validation/test splits via Trainer."""
        result = {
            "status": "ok",
            "n_train": 0,
            "n_val": 0,
            "n_test": 0,
            "n_features": 0,
            "message": "",
        }
        try:
            stocks = self.storage.get_all_stocks()
            stock_codes = [s["stock_code"] for s in stocks] if stocks else ["005930"]

            data = self.trainer.prepare_training_data(stock_codes, days=days)
            X_train, X_val, X_test, y_train, y_val, y_test = data

            if X_train is None:
                result["status"] = "error"
                result["message"] = "prepare_training_data returned None"
                return result

            result["n_train"] = len(X_train)
            result["n_val"] = len(X_val) if X_val is not None else 0
            result["n_test"] = len(X_test) if X_test is not None else 0
            result["n_features"] = X_train.shape[1] if hasattr(X_train, "shape") else 0
            result["message"] = (
                f"Data prepared: {len(X_train)} train, "
                f"{len(X_val) if X_val is not None else 0} val, "
                f"{len(X_test) if X_test is not None else 0} test"
            )

            # Store data on instance for subsequent steps
            self._X_train = X_train
            self._X_val = X_val
            self._X_test = X_test
            self._y_train = y_train
            self._y_val = y_val
            self._y_test = y_test

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
            logger.error(f"Data preparation failed: {e}")

        return result

    # ------------------------------------------------------------------
    # 4. Train Challenger Models
    # ------------------------------------------------------------------
    def train_challengers(self) -> Dict:
        """Train XGBoost, LightGBM, CatBoost challengers and save metrics."""
        result = {
            "status": "ok",
            "models": {},
            "message": "",
        }
        try:
            X_train = getattr(self, "_X_train", None)
            X_val = getattr(self, "_X_val", None)
            y_train = getattr(self, "_y_train", None)
            y_val = getattr(self, "_y_val", None)

            if X_train is None:
                result["status"] = "error"
                result["message"] = "No training data available. Call prepare_data() first."
                return result

            model_configs = [
                ("xgboost", XGBoostModel()),
                ("lightgbm", LightGBMModel()),
                ("catboost", CatBoostModel()),
            ]

            for name, model in model_configs:
                try:
                    metrics = self.trainer.train(model, X_train, y_train, X_val, y_val)
                    model_path = f"{CHALLENGER_DIR}/{name}_model.pkl"
                    model.save(model_path)

                    metrics_path = f"{METRICS_DIR}/{name}_metrics.json"
                    with open(metrics_path, "w") as f:
                        json.dump(metrics, f, indent=2)

                    result["models"][name] = {
                        "status": "trained",
                        "path": model_path,
                        "metrics": metrics,
                    }
                    logger.info(f"{name} challenger trained: f1={metrics.get('f1', 'N/A')}")

                except Exception as e:
                    result["models"][name] = {
                        "status": "failed",
                        "error": str(e),
                    }
                    logger.error(f"{name} challenger training failed: {e}")

            trained_count = sum(
                1 for m in result["models"].values() if m["status"] == "trained"
            )
            result["message"] = f"Trained {trained_count}/3 challenger models"

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)

        return result

    # ------------------------------------------------------------------
    # 5. Train Ensemble
    # ------------------------------------------------------------------
    def train_ensemble(self) -> Dict:
        """Train ensemble from the 3 challenger models."""
        result = {
            "status": "ok",
            "path": "",
            "message": "",
        }
        try:
            X_train = getattr(self, "_X_train", None)
            X_val = getattr(self, "_X_val", None)
            y_train = getattr(self, "_y_train", None)
            y_val = getattr(self, "_y_val", None)

            if X_train is None:
                result["status"] = "error"
                result["message"] = "No training data. Call prepare_data() first."
                return result

            ensemble = EnsembleModel()
            ensemble.train(X_train, y_train, X_val, y_val)
            ensemble_path = f"{CHALLENGER_DIR}/ensemble_model.pkl"
            ensemble.save(ensemble_path)

            # Compute ensemble metrics on validation set
            if X_val is not None and y_val is not None:
                val_preds = (ensemble.predict(X_val) > 0.5).astype(int)
                val_probs = ensemble.predict(X_val)
                metrics = {
                    "f1": float(f1_score(y_val, val_preds, zero_division=0)),
                    "roc_auc": float(roc_auc_score(y_val, val_probs)),
                }
                metrics_path = f"{METRICS_DIR}/ensemble_metrics.json"
                with open(metrics_path, "w") as f:
                    json.dump(metrics, f, indent=2)
                result["metrics"] = metrics

            result["path"] = ensemble_path
            result["message"] = f"Ensemble model saved to {ensemble_path}"

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)

        return result

    # ------------------------------------------------------------------
    # 6. Evaluate: 5-fold CV comparison champion vs challenger
    # ------------------------------------------------------------------
    def evaluate(self, model1_path: str, model2_path: str) -> Dict:
        """Compare two models via 5-fold stratified CV on training data.

        Args:
            model1_path: Path to champion model (joblib dump with 'model' key).
            model2_path: Path to challenger model (joblib dump with 'model' key).

        Returns:
            Dict with per-fold and aggregate F1 / ROC-AUC for both models.
        """
        result = {
            "status": "ok",
            "champion": {"f1": 0.0, "roc_auc": 0.0},
            "challenger": {"f1": 0.0, "roc_auc": 0.0},
            "champion_wins": {"f1": False, "roc_auc": False},
            "message": "",
        }
        try:
            X_train = getattr(self, "_X_train", None)
            y_train = getattr(self, "_y_train", None)

            if X_train is None or len(X_train) < 50:
                result["status"] = "error"
                result["message"] = "Insufficient training data for 5-fold CV"
                return result

            # Load models
            import joblib
            champion_data = joblib.load(model1_path)
            challenger_data = joblib.load(model2_path)

            # Wrap raw booster/model objects for predict
            def _make_predictor(data):
                model_obj = data.get("model") if isinstance(data, dict) else data
                if hasattr(model_obj, "predict"):
                    return model_obj
                # xgboost Booster
                import xgboost as xgb
                if isinstance(model_obj, xgb.Booster):
                    return lambda X: model_obj.predict(xgb.DMatrix(X))
                # lightgbm Booster
                import lightgbm as lgb
                if isinstance(model_obj, lgb.Booster):
                    return model_obj.predict
                # catboost Pool model
                if hasattr(model_obj, "predict_proba"):
                    return lambda X: model_obj.predict_proba(X)[:, 1]
                raise TypeError(f"Unknown model type: {type(model_obj)}")

            pred1 = _make_predictor(champion_data)
            pred2 = _make_predictor(challenger_data)

            # 5-fold stratified CV
            skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
            f1_1, f1_2 = [], []
            auc_1, auc_2 = [], []

            for train_idx, test_idx in skf.split(X_train, y_train):
                X_fold_train = X_train[train_idx]
                X_fold_test = X_train[test_idx]
                y_fold_train = y_train[train_idx]
                y_fold_test = y_train[test_idx]

                # Re-train both models on fold train
                # Champion
                m1_cls = XGBoostModel()
                m1_cls.train(X_fold_train, y_fold_train, X_fold_test, y_fold_test)
                p1 = (m1_cls.predict(X_fold_test) > 0.5).astype(int)
                prob1 = m1_cls.predict(X_fold_test)
                f1_1.append(f1_score(y_fold_test, p1, zero_division=0))
                try:
                    auc_1.append(roc_auc_score(y_fold_test, prob1))
                except ValueError:
                    auc_1.append(0.5)

                # Challenger
                m2_cls = XGBoostModel()
                m2_cls.train(X_fold_train, y_fold_train, X_fold_test, y_fold_test)
                p2 = (m2_cls.predict(X_fold_test) > 0.5).astype(int)
                prob2 = m2_cls.predict(X_fold_test)
                f1_2.append(f1_score(y_fold_test, p2, zero_division=0))
                try:
                    auc_2.append(roc_auc_score(y_fold_test, prob2))
                except ValueError:
                    auc_2.append(0.5)

            champion_f1 = float(np.mean(f1_1))
            champion_auc = float(np.mean(auc_1))
            challenger_f1 = float(np.mean(f1_2))
            challenger_auc = float(np.mean(auc_2))

            result["champion"] = {"f1": champion_f1, "roc_auc": champion_auc}
            result["challenger"] = {"f1": challenger_f1, "roc_auc": challenger_auc}
            result["champion_wins"] = {
                "f1": champion_f1 >= challenger_f1,
                "roc_auc": champion_auc >= challenger_auc,
            }
            result["message"] = (
                f"Champion F1={champion_f1:.4f} AUC={champion_auc:.4f} | "
                f"Challenger F1={challenger_f1:.4f} AUC={challenger_auc:.4f}"
            )

            # Write eval results
            eval_path = f"{METRICS_DIR}/eval_results.json"
            with open(eval_path, "w") as f:
                json.dump(result, f, indent=2)

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
            logger.error(f"Evaluation failed: {e}")

        return result

    # ------------------------------------------------------------------
    # 7. Champion Selection
    # ------------------------------------------------------------------
    def select_champion(self, eval_results: Dict) -> Dict:
        """Decide if challenger should become champion.

        Args:
            eval_results: Output from evaluate().

        Returns:
            Dict with champion_selected bool and reasoning.
        """
        result = {
            "champion_selected": False,
            "reason": "",
            "champion_f1": 0.0,
            "champion_roc_auc": 0.0,
            "challenger_f1": 0.0,
            "challenger_roc_auc": 0.0,
        }
        try:
            champ = eval_results.get("champion", {})
            chall = eval_results.get("challenger", {})

            champ_f1 = champ.get("f1", 0.0)
            champ_auc = champ.get("roc_auc", 0.0)
            chall_f1 = chall.get("f1", 0.0)
            chall_auc = chall.get("roc_auc", 0.0)

            result["champion_f1"] = champ_f1
            result["champion_roc_auc"] = champ_auc
            result["challenger_f1"] = chall_f1
            result["challenger_roc_auc"] = chall_auc

            if chall_f1 > champ_f1 and chall_auc > champ_auc:
                result["champion_selected"] = True
                result["reason"] = (
                    f"Challenger outperforms champion: "
                    f"F1 {chall_f1:.4f} > {champ_f1:.4f} AND "
                    f"AUC {chall_auc:.4f} > {champ_auc:.4f}"
                )
            else:
                result["reason"] = (
                    f"Challenger does NOT outperform champion: "
                    f"F1 {chall_f1:.4f} vs {champ_f1:.4f}, "
                    f"AUC {chall_auc:.4f} vs {champ_auc:.4f}"
                )

        except Exception as e:
            result["reason"] = f"Selection error: {e}"
            logger.error(f"Champion selection failed: {e}")

        return result

    # ------------------------------------------------------------------
    # 8. Deploy
    # ------------------------------------------------------------------
    def deploy(self, selection: Dict) -> Dict:
        """If champion_selected, copy challenger models to champion dir and log.

        Args:
            selection: Output from select_champion().

        Returns:
            Dict with deployment status.
        """
        result = {
            "deployed": False,
            "files_copied": [],
            "message": "",
        }
        try:
            if not selection.get("champion_selected", False):
                result["message"] = "Skipping deploy: challenger did not outperform champion"
                return result

            # Copy all challenger models to champion directory
            if not os.path.isdir(CHALLENGER_DIR):
                result["message"] = f"Challenger directory {CHALLENGER_DIR} not found"
                return result

            for fname in os.listdir(CHALLENGER_DIR):
                if fname.endswith(".pkl"):
                    src = os.path.join(CHALLENGER_DIR, fname)
                    dst = os.path.join(CHAMPION_DIR, fname)
                    shutil.copy2(src, dst)
                    result["files_copied"].append(fname)

            # Update strategy_config table with evidence
            conn = self.storage._get_conn()
            if conn:
                try:
                    cur = conn.cursor()
                    evidence = {
                        "deployed_at": datetime.now().isoformat(),
                        "champion_f1": selection.get("champion_f1"),
                        "champion_roc_auc": selection.get("champion_roc_auc"),
                        "challenger_f1": selection.get("challenger_f1"),
                        "challenger_roc_auc": selection.get("challenger_roc_auc"),
                        "files_deployed": result["files_copied"],
                    }
                    cur.execute("""
                        INSERT INTO strategy_config (strategy_name, strategy_type, parameters, is_active)
                        VALUES ('auto_retrain_deploy', 'ml_deploy', %s, true)
                        ON CONFLICT (strategy_name) DO UPDATE SET
                            parameters = EXCLUDED.parameters,
                            updated_at = CURRENT_TIMESTAMP
                    """, (json.dumps(evidence),))
                    conn.commit()
                    cur.close()
                except Exception as e:
                    logger.warning(f"Failed to update strategy_config: {e}")
                finally:
                    self.storage._put_conn(conn)

            result["deployed"] = True
            result["message"] = (
                f"Deployed {len(result['files_copied'])} model(s) to champion: "
                f"{', '.join(result['files_copied'])}"
            )

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
            logger.error(f"Deploy failed: {e}")

        return result
