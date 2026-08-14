"""News event embedding via sentence-transformers (Phase 4).

``NewsEmbedder`` lazily loads ``paraphrase-multilingual-MiniLM-L12-v2``
(384-dim) on first ``embed`` call so tests can skip model loading when the
network/CI cannot reach HuggingFace. Model load failure is logged and retried
on the next call; ``embed`` returns ``None`` on failure (fail-open, optional
column).
"""

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384


class NewsEmbedder:
    """Lazy-loading sentence-transformer embedder for core_event_text."""

    def __init__(self, model_name: str = MODEL_NAME):
        self._model_name = model_name
        self._model = None

    def _load_model(self):
        """Load the sentence transformer model (lazy, once)."""
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            logger.info("Loaded sentence transformer model: %s", self._model_name)
        except Exception as e:
            logger.error(
                "Failed to load sentence transformer '%s': %s", self._model_name, e
            )
            self._model = None
        return self._model

    def embed(self, core_event_text: str) -> Optional[np.ndarray]:
        """Embed ``core_event_text`` into a 384-dim float32 vector.

        Returns ``None`` if the model cannot be loaded (fail-open). The model
        is loaded lazily on first call and retried on subsequent calls if the
        previous load failed.
        """
        if not core_event_text:
            return None
        model = self._load_model()
        if model is None:
            return None
        try:
            vec = model.encode(core_event_text, normalize_embeddings=True)
            arr = np.asarray(vec, dtype=np.float32).reshape(-1)
            if arr.shape[0] != EMBEDDING_DIM:
                logger.error(
                    "Unexpected embedding dim %d (expected %d)",
                    arr.shape[0],
                    EMBEDDING_DIM,
                )
                return None
            return arr
        except Exception as e:
            logger.error("Embedding failed for text: %s", e)
            return None
