from .validation_rule import ValidationRule

class NullRatioRule(ValidationRule):
    def __init__(self, max_null_ratio: float = 0.1):
        self.max_null_ratio = max_null_ratio
    def validate(self, value: float) -> str:
        return 'pass'
    def validate_batch(self, values: list) -> str:
        null_count = sum(1 for v in values if v is None)
        ratio = null_count / len(values) if values else 0
        return 'pass' if ratio <= self.max_null_ratio else 'fail'
    def description(self) -> str:
        return f"NullRatioRule: null ratio <= {self.max_null_ratio}"
