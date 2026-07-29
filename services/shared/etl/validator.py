from dataclasses import dataclass, field
from typing import Any

import polars as pl


@dataclass
class ValidationReport:
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    details: list = field(default_factory=list)
    total_checks: int = 0

    def add_pass(self, check: str, message: str = ""):
        self.passed += 1
        self.total_checks += 1
        self.details.append({"check": check, "status": "PASS", "message": message})

    def add_fail(self, check: str, message: str = ""):
        self.failed += 1
        self.total_checks += 1
        self.details.append({"check": check, "status": "FAIL", "message": message})

    def add_warning(self, check: str, message: str = ""):
        self.warnings += 1
        self.total_checks += 1
        self.details.append({"check": check, "status": "WARN", "message": message})

    def all_passed(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        return f"PASS={self.passed} FAIL={self.failed} WARN={self.warnings}"


class SchemaValidator:
    def validate(self, df: pl.DataFrame, schema: dict) -> ValidationReport:
        report = ValidationReport()
        for col_name, col_spec in schema.items():
            check_name = f"schema:{col_name}"
            if col_name not in df.columns:
                report.add_fail(check_name, f"Column '{col_name}' not found")
                continue

            expected_dtype = col_spec.get("dtype")
            if expected_dtype is not None:
                actual_dtype = df.schema[col_name]
                if actual_dtype != expected_dtype:
                    report.add_fail(
                        check_name,
                        f"Column '{col_name}' dtype mismatch: expected {expected_dtype}, got {actual_dtype}",
                    )
                    continue

            nullable = col_spec.get("nullable", True)
            if not nullable:
                null_count = df[col_name].null_count()
                if null_count > 0:
                    report.add_fail(
                        check_name,
                        f"Column '{col_name}' has {null_count} nulls but nullable=False",
                    )
                    continue

            null_ratio = col_spec.get("null_ratio")
            if null_ratio is not None:
                actual_null_ratio = df[col_name].null_count() / len(df)
                if actual_null_ratio > null_ratio:
                    report.add_fail(
                        check_name,
                        f"Column '{col_name}' null_ratio {actual_null_ratio:.4f} exceeds {null_ratio}",
                    )
                    continue

            col_min = col_spec.get("min")
            col_max = col_spec.get("max")
            if col_min is not None or col_max is not None:
                non_null = df[col_name].drop_nulls()
                if len(non_null) > 0:
                    actual_min = non_null.min()
                    actual_max = non_null.max()
                    if col_min is not None and actual_min < col_min:
                        report.add_fail(
                            check_name,
                            f"Column '{col_name}' min {actual_min} < {col_min}",
                        )
                        continue
                    if col_max is not None and actual_max > col_max:
                        report.add_fail(
                            check_name,
                            f"Column '{col_name}' max {actual_max} > {col_max}",
                        )
                        continue

            report.add_pass(check_name, f"Column '{col_name}' schema valid")
        return report


_OPERATORS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "is_null": lambda a, _: a is None,
    "is_not_null": lambda a, _: a is not None,
}


class BusinessRuleValidator:
    def __init__(self):
        self._rules: list[dict] = []
        self._add_market_data_rules()

    def _add_market_data_rules(self):
        market_rules = [
            {"column": "open_price", "condition": ">=", "value": 0, "name": "open_price_ge_0"},
            {"column": "high_price", "condition": ">=", "value": 0, "name": "high_price_ge_0"},
            {"column": "low_price", "condition": ">=", "value": 0, "name": "low_price_ge_0"},
            {"column": "close_price", "condition": ">=", "value": 0, "name": "close_price_ge_0"},
            {"column": "volume", "condition": ">=", "value": 0, "name": "volume_ge_0"},
            {"column": "high_price", "condition": ">=", "compare_to": "low_price", "name": "high_ge_low"},
            {"column": "high_price", "condition": ">=", "compare_to": "open_price", "name": "high_ge_open"},
            {"column": "high_price", "condition": ">=", "compare_to": "close_price", "name": "high_ge_close"},
        ]
        self._rules.extend(market_rules)

    @property
    def rules(self) -> list[dict]:
        return list(self._rules)

    def add_rule(self, rule: dict):
        self._rules.append(rule)

    def validate(self, df: pl.DataFrame, rules: list | None = None) -> ValidationReport:
        report = ValidationReport()
        active_rules = rules if rules is not None else self._rules

        for rule in active_rules:
            col = rule.get("column")
            name = rule.get("name", f"rule:{col}:{rule.get('condition')}:{rule.get('value', '')}")
            condition = rule.get("condition")
            value = rule.get("value")

            if col not in df.columns:
                report.add_fail(name, f"Column '{col}' not found")
                continue

            op = _OPERATORS.get(condition)
            if op is None:
                report.add_warning(name, f"Unknown condition '{condition}'")
                continue

            compare_to = rule.get("compare_to")
            if compare_to is not None:
                if compare_to not in df.columns:
                    report.add_fail(name, f"Compare column '{compare_to}' not found")
                    continue
                col_a = df[col].drop_nulls()
                col_b = df[compare_to].drop_nulls()
                min_len = min(len(col_a), len(col_b))
                violations = sum(
                    1
                    for a, b in zip(col_a[:min_len], col_b[:min_len])
                    if not op(a, b)
                )
            else:
                violations = df[col].drop_nulls().map_elements(lambda x: not op(x, value), return_dtype=pl.Boolean).sum()

            if violations > 0:
                report.add_fail(name, f"{violations} violations in '{col}' ({condition} {value or compare_to})")
            else:
                report.add_pass(name, f"All rows satisfy '{col}' {condition} {value or compare_to}")

        return report


class StatisticalValidator:
    def __init__(self, n_bins: int = 10):
        self.n_bins = n_bins
        self._baseline: dict[str, list[float]] | None = None

    def validate(self, df: pl.DataFrame, reference_stats: dict | None = None) -> ValidationReport:
        report = ValidationReport()

        num_cols = [c for c, t in df.schema.items() if t in (pl.Float32, pl.Float64, pl.Int32, pl.Int64, pl.UInt32, pl.UInt64)]

        if reference_stats is None:
            baseline = {}
            for col in num_cols:
                arr = df[col].drop_nulls().to_list()
                if len(arr) == 0:
                    report.add_warning(f"psi:{col}", f"Column '{col}' has no non-null values")
                    continue
                if len(set(arr)) == 1:
                    edges = [arr[0]] * (self.n_bins + 1)
                else:
                    edges = [min(arr) + (max(arr) - min(arr)) * i / self.n_bins for i in range(self.n_bins + 1)]
                counts = [0] * self.n_bins
                for v in arr:
                    for i in range(self.n_bins):
                        lo = edges[i]
                        hi = edges[i + 1]
                        if i == self.n_bins - 1:
                            if lo <= v <= hi:
                                counts[i] += 1
                                break
                        else:
                            if lo <= v < hi:
                                counts[i] += 1
                                break
                total = len(arr)
                baseline[col] = [c / total for c in counts] if total > 0 else [0.0] * self.n_bins
            self._baseline = baseline
            report.add_pass("psi:baseline", f"Baseline computed for {len(baseline)} columns from current df")
            return report

        for col in num_cols:
            expected = reference_stats.get(col)
            if expected is None:
                report.add_warning(f"psi:{col}", f"No reference stats for '{col}'")
                continue

            arr = df[col].drop_nulls().to_list()
            if len(arr) == 0:
                report.add_warning(f"psi:{col}", f"Column '{col}' has no non-null values")
                continue

            n = self.n_bins
            if len(set(arr)) == 1:
                edges = [arr[0]] * (n + 1)
            else:
                mn, mx = min(arr), max(arr)
                edges = [mn + (mx - mn) * i / n for i in range(n + 1)]

            counts = [0] * n
            for v in arr:
                for i in range(n):
                    lo = edges[i]
                    hi = edges[i + 1]
                    if i == n - 1:
                        if lo <= v <= hi:
                            counts[i] += 1
                            break
                    else:
                        if lo <= v < hi:
                            counts[i] += 1
                            break
            total = len(arr)
            actual = [c / total for c in counts]

            psi = 0.0
            for a, e in zip(actual, expected):
                if a == 0:
                    continue
                if e == 0:
                    psi = float("inf")
                    break
                psi += (a - e) * __import__("math").log(a / e)

            drift = psi > 0.2
            msg = f"PSI={psi:.4f} for '{col}'{' DRIFT DETECTED' if drift else ''}"
            if drift:
                report.add_warning(f"psi:{col}", msg)
            else:
                report.add_pass(f"psi:{col}", msg)

        return report


class CrossSourceValidator:
    def validate(
        self,
        source_a_df: pl.DataFrame,
        source_b_df: pl.DataFrame,
        join_columns: list,
        compare_columns: list,
        tolerance: float = 0.05,
    ) -> ValidationReport:
        report = ValidationReport()

        for c in join_columns:
            if c not in source_a_df.columns:
                report.add_fail(f"join_col:{c}", f"Column '{c}' not in source_a")
            if c not in source_b_df.columns:
                report.add_fail(f"join_col:{c}", f"Column '{c}' not in source_b")
        if report.failed > 0:
            return report

        joined = source_a_df.join(source_b_df, on=join_columns, suffix="_b", how="inner")
        if len(joined) == 0:
            report.add_warning("join", "No matching rows after join")
            return report

        total_checks = 0
        mismatches = 0
        for col in compare_columns:
            col_b = f"{col}_b"
            if col not in joined.columns:
                report.add_fail(f"compare:{col}", f"Column '{col}' not found in result")
                continue
            if col_b not in joined.columns:
                report.add_fail(f"compare:{col}", f"Column '{col_b}' not found in result")
                continue

            a_vals = joined[col]
            b_vals = joined[col_b]
            for i in range(len(joined)):
                av = a_vals[i]
                bv = b_vals[i]
                if av is None or bv is None:
                    mismatches += 1
                    total_checks += 1
                    continue
                denom = max(abs(av), abs(bv), 1.0)
                rel_diff = abs(av - bv) / denom
                total_checks += 1
                if rel_diff >= tolerance:
                    mismatches += 1

        rate = mismatches / total_checks if total_checks > 0 else 0.0
        msg = f"Mismatches={mismatches}/{total_checks} rate={rate:.4f}"
        if mismatches > 0:
            report.add_warning("cross_source", msg)
        else:
            report.add_pass("cross_source", msg)

        return report


class ValidatorPipeline:
    def __init__(self):
        self._validators: dict[str, Any] = {}

    def add_validator(self, name: str, validator):
        self._validators[name] = validator

    def run_all(self, df, **kwargs) -> dict:
        results = {}
        for name, validator in self._validators.items():
            vkwargs = kwargs.get(name, {})
            if vkwargs:
                results[name] = validator.validate(df, **vkwargs)
            else:
                results[name] = validator.validate(df)
        return results

    def run_all_with_summary(self, df, **kwargs) -> tuple[dict, ValidationReport]:
        per_validator = self.run_all(df, **kwargs)
        composite = ValidationReport()
        for name, report in per_validator.items():
            for d in report.details:
                composite.total_checks += 1
                if d["status"] == "PASS":
                    composite.passed += 1
                elif d["status"] == "FAIL":
                    composite.failed += 1
                elif d["status"] == "WARN":
                    composite.warnings += 1
                composite.details.append({**d, "validator": name})
        return per_validator, composite
