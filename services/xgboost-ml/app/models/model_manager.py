"""
Model Manager
Manages model versions and lifecycle.
"""

import logging
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages ML model versions."""

    def __init__(self, storage):
        self.storage = storage

    def get_active_model_version(self) -> Optional[str]:
        """Get currently active model version."""
        try:
            return self.storage.get_active_model_version()
        except Exception as e:
            logger.error(f"Failed to get active version: {e}")
            return None

    def save_model_version(self, version: str, metrics: Dict):
        """Save model version and metrics."""
        try:
            self.storage.save_model_version({
                "version": version,
                "metrics": metrics,
                "created_at": datetime.now().isoformat(),
            })
            logger.info(f"Saved model version {version}: {metrics}")
        except Exception as e:
            logger.error(f"Failed to save model version: {e}")
