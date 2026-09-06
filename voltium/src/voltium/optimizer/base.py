"""Optimizer, objective and constraint ABCs, in atium's shape.

atium builds `cp.Problem(objective.build(w, **kwargs), [c.build(w, **kwargs)
for c in constraints])`; so does voltium. The difference is the kwargs
vocabulary, which is fixed here so every term reads the same names:

    alphas       (n,)   expected daily P&L per dollar vega
    covariance   (n,n)  daily covariance of P&L per dollar vega
    loadings     (n,k)  factor loadings
    factors      list[str]
    gap_freq     (n,)   trailing frequency of |stock return| > 3 sigma
    previous     (n,)   current dollar vega per name
    symbols      list[str]

and `w` is the (n,) CVXPY variable in **dollar vega**.

An objective term returns an expression to be *maximised*; `MVO` sums the
terms. A constraint returns one CVXPY constraint or a list of them.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

import cvxpy as cp
import polars as pl

from voltium.risk_model.base import RiskModel


class Objective(ABC):
    @abstractmethod
    def build(self, weights: cp.Variable, **kwargs) -> cp.Expression:
        """An expression to maximise (penalties return a negative expression)."""


class OptimizerConstraint(ABC):
    @abstractmethod
    def build(self, weights: cp.Variable, **kwargs) -> cp.Constraint | list[cp.Constraint]: ...


class OptimizationError(RuntimeError):
    """Raised when the solver does not return an optimal point."""


class Optimizer(ABC):
    @abstractmethod
    def optimize(
        self,
        date_: dt.date,
        alphas_df: pl.DataFrame,
        risk_model: RiskModel,
        previous_df: pl.DataFrame | None = None,
        gap_freq_df: pl.DataFrame | None = None,
    ) -> pl.DataFrame:
        """`(symbol, target_vega)` in dollar vega for the risk model's symbols.

        `previous_df`: (symbol, dollar_vega) currently held; `gap_freq_df`:
        (symbol, gap_freq). Missing names in either default to zero.
        """
