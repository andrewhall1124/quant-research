"""Objective terms. `MVO` maximises their sum.

    MaxUtility        alpha'w - lambda * w' Sigma w
    TurnoverPenalty   - c * ||w - w_prev||_1
    JumpPenalty       - J * sum_i max(-w_i, 0) * gap_freq_i

Units: `w` is dollar vega, so `alpha'w` is dollars per day and `w' Sigma w`
dollars squared per day. `lambda` therefore has units of 1/dollars; with a
book of tens of thousands of dollars of gross vega and per-vega daily vols
around one vol point, lambda in the 1e-4 .. 1e-3 range gives a well-posed
problem. `c` is a cost per dollar of vega traded, in the same units as
alpha (P&L per dollar vega): the half-spread of a straddle divided by its
dollar vega is the natural calibration. `J` is likewise per dollar of vega.
"""

from __future__ import annotations

from dataclasses import dataclass

import cvxpy as cp
import numpy as np

from voltium.optimizer.base import Objective


@dataclass(frozen=True)
class MaxUtility(Objective):
    risk_aversion: float

    def build(self, weights: cp.Variable, **kwargs) -> cp.Expression:
        alphas: np.ndarray = kwargs["alphas"]
        covariance: np.ndarray = kwargs["covariance"]
        return alphas @ weights - self.risk_aversion * cp.quad_form(weights, cp.psd_wrap(covariance))


@dataclass(frozen=True)
class TurnoverPenalty(Objective):
    cost: float

    def build(self, weights: cp.Variable, **kwargs) -> cp.Expression:
        previous: np.ndarray = kwargs["previous"]
        return -self.cost * cp.norm1(weights - previous)


@dataclass(frozen=True)
class JumpPenalty(Objective):
    """Penalise short vega in names that gap: J * sum(max(-w, 0) * gap_freq)."""

    penalty: float

    def build(self, weights: cp.Variable, **kwargs) -> cp.Expression:
        gap_freq: np.ndarray = kwargs["gap_freq"]
        return -self.penalty * (gap_freq @ cp.pos(-weights))
