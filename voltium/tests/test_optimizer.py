import datetime as dt

import numpy as np
import polars as pl
import pytest

from voltium.optimizer.base import OptimizationError
from voltium.optimizer.constraints import GrossShortVegaCap, NetVegaNeutral, PerNameVegaCap
from voltium.optimizer.mvo import MVO
from voltium.optimizer.objectives import MaxUtility, TurnoverPenalty
from voltium.risk_model.base import RiskModel


class ToyRiskModel(RiskModel):
    def __init__(self, symbols, covariance, loadings=None):
        self.symbol_list = list(symbols)
        self.cov = np.asarray(covariance, dtype=float)
        self.load = np.asarray(loadings, dtype=float) if loadings is not None else np.zeros((len(symbols), 1))

    @property
    def symbols(self):
        return self.symbol_list

    @property
    def factors(self):
        return ["market"]

    def covariance(self, symbols):
        idx = [self.symbol_list.index(s) for s in symbols]
        return self.cov[np.ix_(idx, idx)]

    def loadings(self, symbols):
        idx = [self.symbol_list.index(s) for s in symbols]
        return self.load[idx]

    def idio_vol(self, symbols):
        return np.sqrt(np.diag(self.covariance(symbols)))


def test_three_name_problem_matches_closed_form():
    # max alpha'w - lambda w'Sigma w  s.t. sum(w) = 0, Sigma = s^2 I
    # => w_i = (alpha_i - mean(alpha)) / (2 lambda s^2)
    symbols = ["A", "B", "C"]
    alpha = np.array([0.03, -0.01, 0.01])
    s2 = 0.04
    lam = 2.0
    model = ToyRiskModel(symbols, s2 * np.eye(3))
    alphas_df = pl.DataFrame({"symbol": symbols, "alpha": alpha})
    mvo = MVO([MaxUtility(lam)], [NetVegaNeutral(0.0)])
    out = mvo.optimize(dt.date(2024, 1, 2), alphas_df, model)
    expected = (alpha - alpha.mean()) / (2 * lam * s2)
    assert np.allclose(out["target_vega"].to_numpy(), expected, atol=1e-4)


def test_caps_bind_and_turnover_penalty_holds_position():
    symbols = ["A", "B", "C"]
    alpha = np.array([0.03, -0.01, 0.01])
    model = ToyRiskModel(symbols, 0.04 * np.eye(3))
    alphas_df = pl.DataFrame({"symbol": symbols, "alpha": alpha})
    mvo = MVO([MaxUtility(2.0)], [NetVegaNeutral(0.0), PerNameVegaCap(0.05), GrossShortVegaCap(0.05)])
    out = mvo.optimize(dt.date(2024, 1, 2), alphas_df, model)
    w = out["target_vega"].to_numpy()
    assert abs(w.sum()) < 1e-6
    assert np.all(np.abs(w) <= 0.05 + 1e-6)
    assert np.maximum(-w, 0).sum() <= 0.05 + 1e-6

    # a huge turnover penalty pins the solution to the previous book
    previous_df = pl.DataFrame({"symbol": symbols, "dollar_vega": [0.01, -0.01, 0.0]})
    pinned = MVO([MaxUtility(2.0), TurnoverPenalty(100.0)], [NetVegaNeutral(0.0)]).optimize(
        dt.date(2024, 1, 2), alphas_df, model, previous_df
    )
    assert np.allclose(pinned["target_vega"].to_numpy(), [0.01, -0.01, 0.0], atol=1e-4)


def test_infeasible_problem_raises():
    symbols = ["A", "B"]
    model = ToyRiskModel(symbols, np.eye(2))
    alphas_df = pl.DataFrame({"symbol": symbols, "alpha": [1.0, 1.0]})
    # net vega must be exactly 0 but every name must be >= 1 short... contradictory caps
    class Impossible(PerNameVegaCap):
        def build(self, weights, **kwargs):
            import cvxpy as cp
            return [weights >= 1.0, cp.sum(weights) == 0]
    with pytest.raises(OptimizationError):
        MVO([MaxUtility(1.0)], [Impossible(1.0)]).optimize(dt.date(2024, 1, 2), alphas_df, model)
