import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.shared.etl.validator import (
    BusinessRuleValidator,
    CrossSourceValidator,
    SchemaValidator,
    StatisticalValidator,
    ValidationReport,
    ValidatorPipeline,
)


def test_validation_report_all_passed():
    r = ValidationReport()
    assert r.all_passed() is True
    assert r.failed == 0

    r.add_fail("check1", "fail msg")
    assert r.all_passed() is False


def test_validation_report_summary():
    r = ValidationReport()
    r.add_pass("p1")
    r.add_fail("f1")
    r.add_warning("w1")
    assert r.summary() == "PASS=1 FAIL=1 WARN=1"


def test_validation_report_counts():
    r = ValidationReport()
    r.add_pass("a")
    r.add_pass("b")
    r.add_fail("c")
    r.add_warning("d")
    assert r.passed == 2
    assert r.failed == 1
    assert r.warnings == 1
    assert r.total_checks == 4
    assert len(r.details) == 4


def test_schema_validator_passes():
    df = pl.DataFrame({"price": [1.0, 2.0, 3.0], "qty": [10, 20, 30]})
    schema = {"price": {"dtype": pl.Float64, "nullable": False}, "qty": {"dtype": pl.Int64}}
    v = SchemaValidator()
    r = v.validate(df, schema)
    assert r.all_passed(), r.summary()


def test_schema_validator_missing_column():
    df = pl.DataFrame({"price": [1.0, 2.0]})
    schema = {"price": {}, "volume": {}}
    v = SchemaValidator()
    r = v.validate(df, schema)
    assert not r.all_passed()
    assert any("volume" in d["message"] for d in r.details)


def test_schema_validator_dtype_mismatch():
    df = pl.DataFrame({"price": [1, 2, 3]})
    schema = {"price": {"dtype": pl.Float64}}
    v = SchemaValidator()
    r = v.validate(df, schema)
    assert not r.all_passed()


def test_schema_validator_nullable():
    df = pl.DataFrame({"price": [1.0, None, 3.0]})
    schema = {"price": {"dtype": pl.Float64, "nullable": False}}
    v = SchemaValidator()
    r = v.validate(df, schema)
    assert not r.all_passed()


def test_schema_validator_null_ratio():
    df = pl.DataFrame({"price": [1.0, None, None, 4.0]})
    schema = {"price": {"null_ratio": 0.3}}
    v = SchemaValidator()
    r = v.validate(df, schema)
    assert not r.all_passed()


def test_schema_validator_value_range():
    df = pl.DataFrame({"price": [100.0, 200.0, 999999.0]})
    schema = {"price": {"dtype": pl.Float64, "min": 0, "max": 1000000}}
    v = SchemaValidator()
    r = v.validate(df, schema)
    assert r.all_passed()


def test_schema_validator_value_range_fail():
    df = pl.DataFrame({"price": [-1.0, 200.0]})
    schema = {"price": {"min": 0, "max": 1000000}}
    v = SchemaValidator()
    r = v.validate(df, schema)
    assert not r.all_passed()


def test_business_rule_validator_builtin_rules():
    df = pl.DataFrame({
        "open_price": [100.0, 200.0, 150.0],
        "high_price": [110.0, 210.0, 160.0],
        "low_price": [90.0, 190.0, 140.0],
        "close_price": [105.0, 205.0, 155.0],
        "volume": [10000, 20000, 15000],
    })
    v = BusinessRuleValidator()
    r = v.validate(df)
    assert r.all_passed(), r.summary()


def test_business_rule_validator_volume_violation():
    df = pl.DataFrame({
        "high_price": [110.0],
        "low_price": [90.0],
        "volume": [-1],
    })
    v = BusinessRuleValidator()
    r = v.validate(df)
    assert not r.all_passed()


def test_business_rule_validator_high_ge_low_violation():
    df = pl.DataFrame({
        "open_price": [100.0],
        "high_price": [80.0],
        "low_price": [90.0],
        "close_price": [95.0],
        "volume": [10000],
    })
    v = BusinessRuleValidator()
    r = v.validate(df)
    assert not r.all_passed()


def test_business_rule_validator_add_custom_rule():
    df = pl.DataFrame({
        "open_price": [100.0],
        "high_price": [110.0],
        "low_price": [90.0],
        "close_price": [105.0],
        "volume": [10000],
        "price": [10.0],
    })
    v = BusinessRuleValidator()
    v.add_rule({"column": "price", "condition": "<", "value": 100, "name": "price_lt_100"})
    r = v.validate(df)
    assert r.all_passed()


def test_business_rule_validator_custom_rule_violation():
    df = pl.DataFrame({"price": [10.0, 200.0]})
    v = BusinessRuleValidator()
    v.add_rule({"column": "price", "condition": "<", "value": 100, "name": "price_lt_100"})
    r = v.validate(df)
    assert not r.all_passed()


def test_business_rule_rules_property():
    v = BusinessRuleValidator()
    rules = v.rules
    assert len(rules) > 0
    assert any(r.get("name") == "open_price_ge_0" for r in rules)


def test_business_rule_unknown_condition():
    df = pl.DataFrame({"price": [1.0]})
    v = BusinessRuleValidator()
    v.add_rule({"column": "price", "condition": "unknown_op", "name": "bad_rule"})
    r = v.validate(df)
    assert r.warnings > 0


def test_statistical_validator_baseline():
    df = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0]})
    v = StatisticalValidator(n_bins=5)
    r = v.validate(df, reference_stats=None)
    assert r.all_passed()
    assert v._baseline is not None


def test_statistical_validator_drift_detected():
    ref = {"x": [0.1] * 10}
    df = pl.DataFrame({"x": [100.0] * 100})
    v = StatisticalValidator(n_bins=10)
    r = v.validate(df, reference_stats=ref)
    assert r.warnings > 0


def test_statistical_validator_no_drift():
    ref = {"x": [0.1] * 10}
    vals = []
    for i in range(10):
        vals.extend([float(i)] * 10)
    df = pl.DataFrame({"x": vals})
    v = StatisticalValidator(n_bins=10)
    r = v.validate(df, reference_stats=ref)
    assert r.all_passed()


def test_cross_source_validator_match():
    a = pl.DataFrame({"id": [1, 2, 3], "price": [100.0, 200.0, 300.0]})
    b = pl.DataFrame({"id": [1, 2, 3], "price": [100.1, 200.1, 300.1]})
    v = CrossSourceValidator()
    r = v.validate(a, b, join_columns=["id"], compare_columns=["price"], tolerance=0.05)
    assert r.all_passed(), r.summary()


def test_cross_source_validator_mismatch():
    a = pl.DataFrame({"id": [1, 2], "price": [100.0, 200.0]})
    b = pl.DataFrame({"id": [1, 2], "price": [150.0, 250.0]})
    v = CrossSourceValidator()
    r = v.validate(a, b, join_columns=["id"], compare_columns=["price"], tolerance=0.05)
    assert r.warnings > 0


def test_cross_source_validator_missing_join_col():
    a = pl.DataFrame({"id": [1], "price": [100.0]})
    b = pl.DataFrame({"idx": [1], "price": [100.0]})
    v = CrossSourceValidator()
    r = v.validate(a, b, join_columns=["id"], compare_columns=["price"])
    assert r.failed > 0


def test_validator_pipeline():
    df = pl.DataFrame({
        "open_price": [100.0],
        "high_price": [110.0],
        "low_price": [90.0],
        "close_price": [105.0],
        "volume": [10000],
        "price": [50.0],
        "qty": [100],
    })
    pipeline = ValidatorPipeline()
    pipeline.add_validator("schema", SchemaValidator())
    pipeline.add_validator("business", BusinessRuleValidator())
    results = pipeline.run_all(df, schema={"schema": {"price": {"dtype": pl.Float64}, "qty": {"dtype": pl.Int64}}})
    assert "schema" in results
    assert "business" in results
    assert all(r.all_passed() for r in results.values())


def test_validator_pipeline_run_all_with_summary():
    df = pl.DataFrame({
        "open_price": [100.0],
        "high_price": [110.0],
        "low_price": [90.0],
        "close_price": [105.0],
        "volume": [10000],
        "price": [50.0],
        "qty": [100],
    })
    pipeline = ValidatorPipeline()
    pipeline.add_validator("schema", SchemaValidator())
    pipeline.add_validator("business", BusinessRuleValidator())
    per_v, composite = pipeline.run_all_with_summary(df, schema={"schema": {"price": {"dtype": pl.Float64}, "qty": {"dtype": pl.Int64}}})
    assert composite.all_passed()
    assert composite.total_checks > 0
    assert all("validator" in d for d in composite.details)


def test_cross_source_mismatch_rate():
    a = pl.DataFrame({"id": [1, 2, 3, 4], "val": [10.0, 20.0, 30.0, 40.0]})
    b = pl.DataFrame({"id": [1, 2, 3, 4], "val": [10.5, 20.5, 30.5, 40.5]})
    v = CrossSourceValidator()
    r = v.validate(a, b, join_columns=["id"], compare_columns=["val"], tolerance=0.01)
    assert r.warnings > 0


def test_business_rule_compare_to_violation():
    df = pl.DataFrame({
        "high_price": [100.0],
        "low_price": [110.0],
    })
    v = BusinessRuleValidator()
    r = v.validate(df)
    fails = [d for d in r.details if "high_ge_low" in d["check"]]
    assert any(d["status"] == "FAIL" for d in fails)


def test_statistical_validator_single_value_column():
    df = pl.DataFrame({"a": [5.0, 5.0, 5.0]})
    v = StatisticalValidator(n_bins=5)
    r = v.validate(df, reference_stats=None)
    assert r.all_passed()


def test_cross_source_no_matching_rows():
    a = pl.DataFrame({"id": [1, 2], "val": [10.0, 20.0]})
    b = pl.DataFrame({"id": [3, 4], "val": [30.0, 40.0]})
    v = CrossSourceValidator()
    r = v.validate(a, b, join_columns=["id"], compare_columns=["val"])
    assert r.warnings > 0


def test_statistical_validator_missing_ref_column():
    df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
    v = StatisticalValidator(n_bins=5)
    r = v.validate(df, reference_stats={"b": [0.2, 0.2, 0.2, 0.2, 0.2]})
    assert r.warnings > 0


def test_business_rule_missing_column():
    df = pl.DataFrame({"price": [1.0]})
    v = BusinessRuleValidator()
    v.add_rule({"column": "nonexistent", "condition": ">", "value": 0, "name": "bad_col"})
    r = v.validate(df)
    assert r.failed > 0


def test_business_rule_compare_to_missing():
    df = pl.DataFrame({"high_price": [100.0]})
    v = BusinessRuleValidator()
    r = v.validate(df)
    fails = [d for d in r.details if d["status"] == "FAIL"]
    assert len(fails) > 0


def test_validation_report_empty():
    r = ValidationReport()
    assert r.passed == 0
    assert r.failed == 0
    assert r.warnings == 0
    assert r.total_checks == 0
    assert r.all_passed() is True
    assert r.summary() == "PASS=0 FAIL=0 WARN=0"


def test_schema_validator_non_numeric_range():
    df = pl.DataFrame({"name": ["a", "b", "c"]})
    schema = {"name": {"dtype": pl.String, "min": "a", "max": "c"}}
    v = SchemaValidator()
    r = v.validate(df, schema)
    assert r.all_passed()
