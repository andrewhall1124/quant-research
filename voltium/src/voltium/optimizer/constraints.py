"""Hard constraints on the dollar-vega vector.

    NetVegaNeutral(tolerance)     |sum(w)| <= tolerance
    FactorNeutral(epsilon)        |B' w| <= epsilon, per factor
    PerNameVegaCap(cap)           |w_i| <= cap
    GrossShortVegaCap(cap)        sum(max(-w_i, 0)) <= cap
    GrossVegaCap(cap)             sum(|w_i|) <= cap

All caps are in dollar vega. `FactorNeutral` can name a subset of factors;
by default it applies to every factor the risk model carries.
"""

from __future__ import annotations

from dataclasses import dataclass

import cvxpy as cp
import numpy as np

from voltium.optimizer.base import OptimizerConstraint


@dataclass(frozen=True)
class NetVegaNeutral(OptimizerConstraint):
    tolerance: float = 0.0

    def build(self, weights: cp.Variable, **kwargs) -> list[cp.Constraint]:
        return [cp.sum(weights) <= self.tolerance, cp.sum(weights) >= -self.tolerance]


@dataclass(frozen=True)
class FactorNeutral(OptimizerConstraint):
    epsilon: float = 0.0
    factors: tuple[str, ...] | None = None

    def build(self, weights: cp.Variable, **kwargs) -> list[cp.Constraint]:
        loadings: np.ndarray = kwargs["loadings"]
        names: list[str] = kwargs["factors"]
        columns = [i for i, f in enumerate(names) if self.factors is None or f in self.factors]
        if not columns:
            return []
        exposure = loadings[:, columns].T @ weights
        return [exposure <= self.epsilon, exposure >= -self.epsilon]


@dataclass(frozen=True)
class PerNameVegaCap(OptimizerConstraint):
    cap: float

    def build(self, weights: cp.Variable, **kwargs) -> cp.Constraint:
        return cp.abs(weights) <= self.cap


@dataclass(frozen=True)
class GrossShortVegaCap(OptimizerConstraint):
    cap: float

    def build(self, weights: cp.Variable, **kwargs) -> cp.Constraint:
        return cp.sum(cp.pos(-weights)) <= self.cap


@dataclass(frozen=True)
class GrossVegaCap(OptimizerConstraint):
    cap: float

    def build(self, weights: cp.Variable, **kwargs) -> cp.Constraint:
        return cp.norm1(weights) <= self.cap
