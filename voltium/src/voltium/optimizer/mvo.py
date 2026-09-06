"""CVXPY mean-variance optimizer over dollar-vega weights.

    maximize  sum(objective terms)
    s.t.      every constraint

Solved with Clarabel by default (ECOS if installed and requested). A status
other than `optimal` / `optimal_inaccurate` raises `OptimizationError`
rather than returning zeros: an infeasible book is a configuration problem
the researcher has to see.
"""

from __future__ import annotations

import datetime as dt
import logging

import cvxpy as cp
import numpy as np
import polars as pl

from voltium.optimizer.base import Objective, OptimizationError, Optimizer, OptimizerConstraint
from voltium.risk_model.base import RiskModel

log = logging.getLogger(__name__)

TARGET_SCHEMA = {"symbol": pl.Utf8, "target_vega": pl.Float64}


def align(symbols: list[str], frame: pl.DataFrame | None, column: str, default: float = 0.0) -> np.ndarray:
    if frame is None or frame.is_empty():
        return np.full(len(symbols), default)
    aligned = pl.DataFrame({"symbol": symbols}).join(frame.select("symbol", column), on="symbol", how="left")
    return aligned[column].fill_null(default).to_numpy().astype(float)


class MVO(Optimizer):
    def __init__(
        self,
        objectives: list[Objective],
        constraints: list[OptimizerConstraint],
        solver: str = "CLARABEL",
        symbols_with_alpha_only: bool = True,
    ) -> None:
        self.objectives = objectives
        self.constraints = constraints
        self.solver = solver
        self.symbols_with_alpha_only = symbols_with_alpha_only

    def optimize(
        self,
        date_: dt.date,
        alphas_df: pl.DataFrame,
        risk_model: RiskModel,
        previous_df: pl.DataFrame | None = None,
        gap_freq_df: pl.DataFrame | None = None,
    ) -> pl.DataFrame:
        symbols = list(risk_model.symbols)
        if self.symbols_with_alpha_only:
            with_alpha = set(alphas_df.filter(pl.col("alpha").is_not_null())["symbol"].to_list())
            held = set(previous_df["symbol"].to_list()) if previous_df is not None else set()
            symbols = [s for s in symbols if s in with_alpha or s in held]
        if not symbols:
            return pl.DataFrame(schema=TARGET_SCHEMA)

        alphas = align(symbols, alphas_df, "alpha")
        previous = align(symbols, previous_df, "dollar_vega")
        gap_freq = align(symbols, gap_freq_df, "gap_freq")
        covariance = risk_model.covariance(symbols)
        loadings = risk_model.loadings(symbols)

        weights = cp.Variable(len(symbols))
        kwargs = dict(
            alphas=alphas,
            covariance=covariance,
            loadings=loadings,
            factors=risk_model.factors,
            gap_freq=gap_freq,
            previous=previous,
            symbols=symbols,
        )
        objective = cp.Maximize(sum(term.build(weights, **kwargs) for term in self.objectives))
        constraints: list[cp.Constraint] = []
        for constraint in self.constraints:
            built = constraint.build(weights, **kwargs)
            constraints.extend(built if isinstance(built, list) else [built])
        problem = cp.Problem(objective, constraints)
        problem.solve(solver=self.solver)
        if problem.status not in ("optimal", "optimal_inaccurate"):
            raise OptimizationError(f"{date_}: solver status {problem.status!r} over {len(symbols)} names")
        if problem.status == "optimal_inaccurate":
            log.warning("%s: solver returned optimal_inaccurate", date_)
        return pl.DataFrame({"symbol": symbols, "target_vega": np.asarray(weights.value, dtype=float)}, schema=TARGET_SCHEMA)
