from sentence_transformers import SentenceTransformer
import numpy as np
import logging
import os

from models.device import (
    detect_torch_device_flags,
    resolve_batch_size,
    resolve_embedding_device,
    resolve_torch_threads,
)

logger = logging.getLogger(__name__)

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingModels:
    """Registry for embedding models."""

    _instance = None
    _model = None
    _loaded_device = None

    @classmethod
    def get_model(cls):
        """Get or load the sentence transformer model."""
        if cls._model is None:
            try:
                # GPU 준비용: env 로 디바이스/배치를 제어한다. 기본값(auto/32)은
                # GPU 가 없는 지금과 동일한 CPU 동작을 유지한다.
                requested = os.getenv("EMBEDDING_DEVICE", "auto")
                batch_size = resolve_batch_size(os.getenv("EMBEDDING_BATCH_SIZE"))
                threads = resolve_torch_threads(
                    os.getenv("EMBEDDING_TORCH_THREADS")
                )
                cuda_available, mps_available = detect_torch_device_flags()
                resolution = resolve_embedding_device(
                    requested, cuda_available, mps_available
                )
                if resolution.warning:
                    logger.warning("Embedding device fallback: %s", resolution.warning)
                if threads is not None:
                    import torch

                    torch.set_num_threads(threads)
                logger.info(
                    "Loading sentence transformer '%s' on device '%s' "
                    "(requested='%s', batch_size=%d, torch_threads=%s)",
                    MODEL_NAME, resolution.device, requested, batch_size,
                    threads if threads is not None else "default",
                )
                cls._loaded_device = resolution.device
                cls._model = SentenceTransformer(MODEL_NAME, device=resolution.device)
            except Exception as e:
                logger.warning(f"Failed to load sentence transformer: {e}")
                cls._model = None
        return cls._model

    @classmethod
    def get_default_dim(cls) -> int:
        """Get default embedding dimension."""
        return 1024
