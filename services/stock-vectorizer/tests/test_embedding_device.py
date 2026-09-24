"""EMBEDDING_DEVICE 해석 순수 함수 단위 테스트 (torch 비의존).

GPU 가 없는 호스트에서도 통과해야 한다. ``resolve_embedding_device`` 는
torch 를 import 하지 않으므로 이 테스트는 가벼운 의존성만으로 실행된다.
"""

from models.device import (
    resolve_batch_size,
    resolve_embedding_device,
    resolve_torch_threads,
)


class TestAutoDevice:
    def test_auto_without_cuda_falls_back_to_cpu(self):
        res = resolve_embedding_device("auto", cuda_available=False)
        assert res.device == "cpu"
        assert res.warning is None

    def test_auto_with_cuda_selects_cuda(self):
        res = resolve_embedding_device("auto", cuda_available=True)
        assert res.device == "cuda"
        assert res.warning is None

    def test_auto_prefers_cuda_over_mps(self):
        res = resolve_embedding_device("auto", cuda_available=True, mps_available=True)
        assert res.device == "cuda"

    def test_auto_with_mps_only_selects_mps(self):
        res = resolve_embedding_device("auto", cuda_available=False, mps_available=True)
        assert res.device == "mps"


class TestExplicitDevice:
    def test_explicit_cpu(self):
        res = resolve_embedding_device("cpu", cuda_available=True)
        assert res.device == "cpu"
        assert res.warning is None

    def test_explicit_cuda_available(self):
        res = resolve_embedding_device("cuda", cuda_available=True)
        assert res.device == "cuda"
        assert res.warning is None

    def test_explicit_cuda_unavailable_falls_back_with_warning(self):
        res = resolve_embedding_device("cuda", cuda_available=False)
        assert res.device == "cpu"
        assert res.warning is not None
        assert "cuda" in res.warning

    def test_explicit_mps_unavailable_falls_back_with_warning(self):
        res = resolve_embedding_device("mps", cuda_available=False, mps_available=False)
        assert res.device == "cpu"
        assert res.warning is not None


class TestUnknownDevice:
    def test_unknown_value_falls_back_to_cpu_with_warning(self):
        res = resolve_embedding_device("tpu", cuda_available=True)
        assert res.device == "cpu"
        assert res.warning is not None

    def test_empty_or_none_means_auto(self):
        assert resolve_embedding_device("", cuda_available=False).device == "cpu"
        assert resolve_embedding_device(None, cuda_available=True).device == "cuda"

    def test_case_insensitive(self):
        assert resolve_embedding_device("CUDA", cuda_available=True).device == "cuda"
        assert resolve_embedding_device("AUTO", cuda_available=False).device == "cpu"


class TestBatchSize:
    def test_default_when_none(self):
        assert resolve_batch_size(None) == 32

    def test_parses_positive_int(self):
        assert resolve_batch_size("64") == 64
        assert resolve_batch_size(" 128 ") == 128

    def test_invalid_falls_back_to_default(self):
        assert resolve_batch_size("abc") == 32
        assert resolve_batch_size("") == 32

    def test_non_positive_falls_back_to_default(self):
        assert resolve_batch_size("0") == 32
        assert resolve_batch_size("-5") == 32


class TestTorchThreads:
    def test_none_means_torch_default(self):
        assert resolve_torch_threads(None) is None

    def test_empty_string_means_torch_default(self):
        assert resolve_torch_threads("") is None
        assert resolve_torch_threads("   ") is None

    def test_parses_positive_int(self):
        assert resolve_torch_threads("4") == 4
        assert resolve_torch_threads(" 8 ") == 8

    def test_zero_returns_none_with_warning(self):
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert resolve_torch_threads("0") is None
        assert len(caught) == 1
        assert issubclass(caught[0].category, UserWarning)

    def test_negative_returns_none_with_warning(self):
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert resolve_torch_threads("-1") is None
        assert len(caught) == 1
        assert issubclass(caught[0].category, UserWarning)

    def test_non_integer_returns_none_with_warning(self):
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert resolve_torch_threads("abc") is None
        assert len(caught) == 1
        assert issubclass(caught[0].category, UserWarning)
