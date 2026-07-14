from .validation_rule import ValidationRule

class RateChangeRule(ValidationRule):
    def __init__(self, max_change_pct: float = 50.0):
        self.max_change_pct = max_change_pct
        self.previous = None
    def set_previous(self, prev_value: float):
        self.previous = prev_value
    def validate(self, value: float) -> str:
        if self.previous is None or self.previous == 0:
            return 'pass'
        change_pct = abs((value - self.previous) / self.previous) * 100
        return 'warn' if change_pct > self.max_change_pct else 'pass'
    def description(self) -> str:
        return f"RateChangeRule: change <= {self.max_change_pct}%"
