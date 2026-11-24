"""feature_liveness_check 판정 경계 테스트.

판정 4분기 경계값을 고정한다: dead(nonzero<0.0005) → sparse(<0.01) → constant(const>0.99) → alive.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("feature_liveness_check",
                                              ROOT / "scripts" / "feature_liveness_check.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_dead_below_half_permille():
    assert mod.classify_feature(0.0005 - 1e-9, 0.0) == "dead"


def test_sparse_starts_at_half_permille():
    assert mod.classify_feature(0.0005, 0.0) == "sparse"


def test_sparse_below_one_percent():
    assert mod.classify_feature(0.01 - 1e-9, 0.0) == "sparse"


def test_constant_above_99_percent():
    assert mod.classify_feature(0.01, 0.99 + 1e-9) == "constant"


def test_alive_at_boundaries():
    assert mod.classify_feature(0.01, 0.99) == "alive"


def test_alive_typical():
    assert mod.classify_feature(0.5, 0.2) == "alive"


def test_dead_wins_over_constant():
    assert mod.classify_feature(0.0, 0.999) == "dead"
