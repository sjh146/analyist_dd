"""Platt scaling calibration wrapper.

Wraps sklearn ``CalibratedClassifierCV`` with ``method='sigmoid'`` to map raw
ensemble probabilities -> calibrated probabilities. Fitting is OFFLINE; the
screener hot loop only calls ``calibrate``.
"""

import logging
import warnings
from typing import Optional, Union

import numpy as np
from sklearn.calibration import CalibratedClassifierCV

logger = logging.getLogger(__name__)


class _ProbPassthrough:
    """Base estimator that returns the supplied raw probabilities as-is.

    ``CalibratedClassifierCV`` needs a base estimator whose ``predict_proba``
    yields the values to be calibrated. Because we already have the ensemble's
    raw probabilities, this passthrough simply echoes them back so the
    calibration mapping (sigmoid / isotonic) is fit on top.
    """

    _estimator_type = "classifier"

    def fit(self, X, y):
        self.classes_ = np.array([0, 1])
        return self

    def predict_proba(self, X):
        p = np.asarray(X, dtype=np.float64).reshape(-1)
        return np.column_stack([1.0 - p, p])


def _validate_probs(probs: Union[np.ndarray, list, float]) -> np.ndarray:
    """Validate a probability array and return it as a 1-D float64 ndarray.

    Raises ``ValueError`` for out-of-range or misordered values so callers
    cannot silently feed garbage into the calibrator.
    """
    arr = np.asarray(probs, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        raise ValueError("probs must be a non-empty array of probabilities")
    if np.any(~np.isfinite(arr)):
        raise ValueError("probs contains NaN or infinite values")
    if np.any(arr < 0.0) or np.any(arr > 1.0):
        raise ValueError("probs must lie within [0, 1]")
    return arr


class PlattCalibrator:
    """Platt (sigmoid) calibration of raw probabilities.

    Example
    -------
    >>> cal = PlattCalibrator()
    >>> cal.fit(probs_train, y_train)
    >>> cal.calibrate(0.7)
    0.62
    """

    def __init__(self, cv: str = "prefit"):
        self.cv = cv
        self._base = _ProbPassthrough()
        self._calibrator: Optional[CalibratedClassifierCV] = None
        self._fitted = False

    def fit(self, probs, y) -> "PlattCalibrator":
        """Fit the sigmoid calibration mapping offline.

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
            estimator=self._base, method="sigmoid", cv=self.cv
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
            raise RuntimeError("PlattCalibrator must be fit before calibrate()")
        p = _validate_probs(probs)
        out = self._calibrator.predict_proba(p.reshape(-1, 1))[:, 1]
        if p.size == 1:
            return float(out[0])
        return out
