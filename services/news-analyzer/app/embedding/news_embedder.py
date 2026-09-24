"""News event embedding via sentence-transformers (Phase 4).

``NewsEmbedder`` lazily loads ``paraphrase-multilingual-MiniLM-L12-v2``
(384-dim) on first ``embed`` call so tests can skip model loading when the
network/CI cannot reach HuggingFace. Model load failure is logged and retried
on the next call; ``embed`` returns ``None`` on failure (fail-open, optional
column).
"""

import logging
import os
from typing import Optional

import numpy as np

from app.embedding.device import (
    detect_torch_device_flags,
    resolve_batch_size,
    resolve_embedding_device,
    resolve_torch_threads,
)

logger = logging.getLogger(__name__)

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384


class NewsEmbedder:
    """Lazy-loading sentence-transformer embedder for core_event_text."""

    def __init__(
        self,
        model_name: str = MODEL_NAME,
        device: Optional[str] = None,
        batch_size: Optional[int] = None,
    ):
        self._model_name = model_name
        self._model = None
        self._loaded_device = None
        # GPU 준비용: env 로 디바이스/배치를 제어한다. 기본값(auto/32)은
        # GPU 가 없는 지금과 동일한 CPU 동작을 유지한다.
        self._device_request = (
            os.getenv("EMBEDDING_DEVICE", "auto") if device is None else device
        )
        self._batch_size = (
            resolve_batch_size(os.getenv("EMBEDDING_BATCH_SIZE"))
            if batch_size is None
            else batch_size
        )
        # CPU 스레드 튜닝용: 미설정이면 None(torch 기본 동작 유지).
        self._threads = resolve_torch_threads(
            os.getenv("EMBEDDING_TORCH_THREADS")
        )

    def _load_model(self):
        """Load the sentence transformer model (lazy, once)."""
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            cuda_available, mps_available = detect_torch_device_flags()
            resolution = resolve_embedding_device(
                self._device_request, cuda_available, mps_available
            )
            if resolution.warning:
                logger.warning("Embedding device fallback: %s", resolution.warning)
            if self._threads is not None:
                import torch

                torch.set_num_threads(self._threads)
            logger.info(
                "Loading sentence transformer '%s' on device '%s' "
                "(requested='%s', batch_size=%d, torch_threads=%s)",
                self._model_name,
                resolution.device,
                self._device_request,
                self._batch_size,
                self._threads if self._threads is not None else "default",
            )
            self._loaded_device = resolution.device
            self._model = SentenceTransformer(
                self._model_name, device=resolution.device
            )
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
            vec = model.encode(
                core_event_text,
                normalize_embeddings=True,
                batch_size=self._batch_size,
            )
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
