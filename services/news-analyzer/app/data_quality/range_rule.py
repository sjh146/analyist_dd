from .validation_rule import ValidationRule

class RangeRule(ValidationRule):
    def __init__(self, min_val: float, max_val: float, name: str = ""):
        self.min_val = min_val
        self.max_val = max_val
        self.name = name or f"RangeRule({min_val},{max_val})"
    def validate(self, value: float) -> str:
        return 'pass' if self.min_val <= value <= self.max_val else 'fail'
    def description(self) -> str:
        return f"{self.name}: value between {self.min_val} and {self.max_val}"
