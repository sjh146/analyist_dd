"""Probability calibration layer.

Maps raw ensemble probabilities -> calibrated probabilities as a SEPARATE
layer on top of ``EnsembleModel.predict``. All fitting is OFFLINE; the
screener hot loop only calls ``calibrate`` (O(1) lookup).

Variants:
- ``PlattCalibrator`` / ``IsotonicCalibrator``: deterministic sklearn
  ``CalibratedClassifierCV`` wrappers.
- ``BayesianCalibrator``: NumPyro NUTS posterior over a Beta-prior Bernoulli
  calibration model, emitting both a calibrated probability and an
  uncertainty (posterior std).
"""

from .platt import PlattCalibrator
from .isotonic import IsotonicCalibrator
from .bayesian_calibration import BayesianCalibrator

__all__ = [
    "PlattCalibrator",
    "IsotonicCalibrator",
    "BayesianCalibrator",
]
