from .validation_rule import ValidationRule

class ZScoreRule(ValidationRule):
    def __init__(self, threshold: float = 3.0):
        self.threshold = threshold
        self.mean = None
        self.std = None
    def set_stats(self, mean: float, std: float):
        self.mean = mean
        self.std = std
    def validate(self, value: float) -> str:
        if self.mean is None or self.std is None or self.std == 0:
            return 'warn'
        z = abs((value - self.mean) / self.std)
        return 'warn' if z > self.threshold else 'pass'
    def description(self) -> str:
        return f"ZScoreRule: |z| <= {self.threshold}"
