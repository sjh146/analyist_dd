class Validator:
    def __init__(self, rules: list = None):
        self.rules = rules or []
    def add_rule(self, rule):
        self.rules.append(rule)
    def validate_value(self, value: float) -> dict:
        return {r.description(): r.validate(value) for r in self.rules}
    def validate_batch(self, values: list) -> dict:
        details = []
        passed = failed = warned = 0
        for r in self.rules:
            # For NullRatioRule, use validate_batch; else validate per value
            if hasattr(r, 'validate_batch') and 'validate_batch' in type(r).__dict__:
                result = r.validate_batch(values)
            else:
                result = r.validate(values[0]) if values else 'warn'
            details.append({'rule': r.description(), 'result': result})
            if result == 'pass': passed += 1
            elif result == 'fail': failed += 1
            else: warned += 1
        return {'passed': passed, 'failed': failed, 'warned': warned, 'details': details}
