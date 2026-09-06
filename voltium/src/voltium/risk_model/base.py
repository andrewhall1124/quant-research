"""Risk model ABCs, in dollar-vega units.

atium's `RiskModel.build_covariance_matrix()` returns the covariance of
asset returns; the weights are fractions of capital. Here the position is
dollar vega and the "return" of one dollar of vega is the daily P&L of the
reference straddle per dollar of vega (`providers/straddle_returns.py`), so
`covariance(symbols)` is the daily covariance of P&L per dollar of vega, and
for a position vector `w` in dollar vega, `w @ Sigma @ w` is the daily P&L
variance in dollars squared.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

import numpy as np


class RiskModel(ABC):
    @property
    @abstractmethod
    def symbols(self) -> list[str]:
        """Every symbol the model can price, sorted."""

    @property
    @abstractmethod
    def factors(self) -> list[str]: ...

    @abstractmethod
    def covariance(self, symbols: list[str]) -> np.ndarray:
        """Daily covariance of P&L per dollar vega, (n x n), in `symbols` order."""

    @abstractmethod
    def loadings(self, symbols: list[str]) -> np.ndarray:
        """Factor loadings, (n x k), in `symbols` x `factors` order."""

    @abstractmethod
    def idio_vol(self, symbols: list[str]) -> np.ndarray:
        """Daily idiosyncratic vol of P&L per dollar vega, (n,)."""


class RiskModelConstructor(ABC):
    @abstractmethod
    def get_risk_model(self, date_: dt.date) -> RiskModel:
        """A risk model estimated on data up to and including `date_`."""
