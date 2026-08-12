"""Isotonic regression calibration wrapper.

Wraps sklearn ``CalibratedClassifierCV`` with ``method='isotonic'`` to map raw
ensemble probabilities -> calibrated probabilities. Fitting is OFFLINE; the
screener hot loop only calls ``calibrate``.
"""

import logging
import warnings
from typing import Optional, Union

import numpy as np
from sklearn.calibration import CalibratedClassifierCV

from .platt import _ProbPassthrough, _validate_probs

logger = logging.getLogger(__name__)


class IsotonicCalibrator:
    """Isotonic regression calibration of raw probabilities.

    Example
    -------
    >>> cal = IsotonicCalibrator()
    >>> cal.fit(probs_train, y_train)
    >>> cal.calibrate(0.7)
    0.65
    """

    def __init__(self, cv: str = "prefit"):
        self.cv = cv
        self._base = _ProbPassthrough()
        self._calibrator: Optional[CalibratedClassifierCV] = None
        self._fitted = False

    def fit(self, probs, y) -> "IsotonicCalibrator":
        """Fit the isotonic calibration mapping offline.

        Parameters
        ----------
        probs : array-like of shape (n_samples,)
            Raw ensemble probabilities used as the calibration input.
        y : array-like of shape (n_samples,)
            Binary ground-truth labels (0/1).
        """
        p = _validate_probs(probs)
        y_arr = np.asarray(y).reshape(-1)
        if p.shape[0] != y_arr.shape[0]:
            raise ValueError("probs and y must have the same length")
        if not np.all(np.isin(y_arr, [0, 1])):
            raise ValueError("y must contain only binary labels 0/1")

        self._base.fit(p.reshape(-1, 1), y_arr)
        self._calibrator = CalibratedClassifierCV(
            estimator=self._base, method="isotonic", cv=self.cv
        )
        # cv='prefit' is deprecated in sklearn 1.6 (removed in 1.8); suppress
        # the harmless deprecation warning while pinned to 1.6.1.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self._calibrator.fit(p.reshape(-1, 1), y_arr)
        self._fitted = True
        return self

    def calibrate(self, probs) -> Union[float, np.ndarray]:
        """Return the calibrated probability for the given raw probability.

        Accepts a scalar or an array. Returns a float for scalar input and an
        ndarray for array input.
        """
        if not self._fitted or self._calibrator is None:
            raise RuntimeError("IsotonicCalibrator must be fit before calibrate()")
        p = _validate_probs(probs)
        out = self._calibrator.predict_proba(p.reshape(-1, 1))[:, 1]
        if p.size == 1:
            return float(out[0])
        return out
