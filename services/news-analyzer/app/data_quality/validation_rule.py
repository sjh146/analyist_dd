from abc import ABC, abstractmethod

class ValidationRule(ABC):
    @abstractmethod
    def validate(self, value: float) -> str:
        """Return 'pass', 'fail', or 'warn'"""
    
    @abstractmethod
    def description(self) -> str:
        ...
