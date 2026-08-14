"""Tests for NewsEmbedder (Phase 4).

Model loading requires network access to HuggingFace. In CI without network,
the model-load tests are skipped (pytest.skip) so the suite stays green.
"""

import numpy as np
import pytest

from app.embedding.news_embedder import NewsEmbedder, EMBEDDING_DIM, MODEL_NAME


@pytest.fixture(scope="module")
def embedder():
    """Return a NewsEmbedder with a loaded model, or skip if unavailable."""
    emb = NewsEmbedder()
    model = emb._load_model()
    if model is None:
        pytest.skip("sentence-transformer model unavailable (no network/CI)")
    return emb


class TestEmbeddingDimension:
    def test_embedding_dim_is_384(self, embedder):
        vec = embedder.embed("삼성전자가 2분기 실적을 발표했습니다")
        assert vec is not None
        assert vec.shape == (EMBEDDING_DIM,)
        assert vec.dtype == np.float32

    def test_constant_dim_exposed(self):
        assert EMBEDDING_DIM == 384
        assert MODEL_NAME == "paraphrase-multilingual-MiniLM-L12-v2"


class TestSimilarity:
    def test_same_text_high_dot_product(self, embedder):
        text = "삼성전자가 2분기 실적을 발표했습니다 영업이익이 증가했습니다"
        v1 = embedder.embed(text)
        v2 = embedder.embed(text)
        assert v1 is not None and v2 is not None
        dot = float(np.dot(v1, v2))
        assert dot > 0.9

    def test_different_text_relatively_lower(self, embedder):
        a = "삼성전자가 2분기 실적을 발표했습니다 영업이익이 증가했습니다"
        b = "현대차가 새로운 전기차 모델을 출시했습니다 주행거리가 늘었습니다"
        va = embedder.embed(a)
        vb = embedder.embed(b)
        assert va is not None and vb is not None
        same_dot = float(np.dot(va, va))
        diff_dot = float(np.dot(va, vb))
        assert diff_dot < same_dot


class TestFailOpen:
    def test_empty_text_returns_none(self):
        emb = NewsEmbedder()
        assert emb.embed("") is None
        assert emb.embed(None) is None
