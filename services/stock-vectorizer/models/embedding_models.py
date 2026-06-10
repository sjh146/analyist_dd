from sentence_transformers import SentenceTransformer
import numpy as np
import logging

logger = logging.getLogger(__name__)


class EmbeddingModels:
    """Registry for embedding models."""

    _instance = None
    _model = None

    @classmethod
    def get_model(cls):
        """Get or load the sentence transformer model."""
        if cls._model is None:
            try:
                cls._model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
                logger.info("Loaded sentence transformer model")
            except Exception as e:
                logger.warning(f"Failed to load sentence transformer: {e}")
                cls._model = None
        return cls._model

    @classmethod
    def get_default_dim(cls) -> int:
        """Get default embedding dimension."""
        return 1024
