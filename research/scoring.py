"""Forecast evaluation, shared by every study in `research/`.

Four questions, four tools. They answer different things and are easy to
conflate, so each lives in its own function:

* `mincer_zarnowitz` — is the forecast *calibrated*? (joint alpha=0, beta=1)
* `LOSSES` — how wrong is it? (squared error and QLIKE, the two losses that
  survive a noisy volatility proxy)
* `diebold_mariano_pair` — is A *more accurate* than B? (test on the loss
  differential, not on two RMSEs side by side)
* an encompassing regression, run by the caller — who knows something the
  others do not?

What is deliberately absent is the t-statistic on `beta = 0`. That null says
only "this forecast carries some information", which anything volatility-shaped
passes against a persistent target, and it ranks forecasts badly: in
`research/volatility/` it puts ARCH (t=3.45, MZ R^2=0.03) above trailing RV
(t=1.88, MZ R^2=0.10).

Standard errors are the caller's decision, passed as a `covariance` dict of
statsmodels `fit()` keyword arguments; `hac_kwargs` and `driscoll_kraay_kwargs`
below build the two that matter here.
"""

import numpy as np
import statsmodels.api as sm


def hac_kwargs(lags: int) -> dict:
    """Newey-West with `lags` lags — for a single overlapping time series."""
    return {"cov_type": "HAC", "cov_kwds": {"maxlags": lags}}


def driscoll_kraay_kwargs(lags: int, time: np.ndarray) -> dict:
    """Driscoll-Kraay: Newey-West *after* summing across the panel by date.

    A pooled panel of many names on the same dates violates the assumption
    behind plain Newey-West in a second way: residuals are correlated *across
    names* on any given day, because every name shares the market factor. HAC
    handles the time dimension and ignores that, so it treats 500 names as 500
    independent observations when they are closer to one. Driscoll-Kraay
    aggregates each period's cross-section into a single moment first, which
    prices the common shock properly.

    `time` is an integer period index, one entry per observation.
    """
    return {"cov_type": "hac-groupsum", "cov_kwds": {"time": time, "maxlags": lags}}


def mincer_zarnowitz(
    target: np.ndarray, forecast: np.ndarray, covariance: dict
) -> dict:
    """Regress target on forecast: is the forecast calibrated?

        target[t+h] = alpha + beta * forecast[t] + error

    A calibrated forecast has alpha = 0 and beta = 1. The coefficients diagnose
    different faults — `beta < 1` means the forecast over-reacts, `alpha != 0`
    with `beta` near 1 is a pure level bias, which is the fixable kind — and the
    test that matters is the joint one, because a forecast can be perfectly
    scaled and systematically five points high.
    """
    design = sm.add_constant(forecast)
    fit = sm.OLS(target, design).fit(**covariance)
    intercept, slope = fit.params[0], fit.params[1]
    restriction = (np.eye(2), np.array([0.0, 1.0]))
    joint = fit.wald_test(restriction, use_f=True, scalar=True)
    if not np.isfinite(joint.pvalue):
        # The panel estimator (hac-groupsum) has no finite-sample F reference
        # distribution, so the F form returns NaN. Fall back to the chi-square
        # form, which is the large-sample version of the same restriction.
        joint = fit.wald_test(restriction, use_f=False, scalar=True)
    return {
        "alpha": intercept,
        "beta": slope,
        "t_alpha_eq_0": intercept / fit.bse[0],
        "t_beta_eq_1": (slope - 1) / fit.bse[1],
        "mz_joint_f": float(joint.statistic),
        "mz_joint_p": float(joint.pvalue),
        "r2": fit.rsquared,
    }


def squared_error_loss(actual: np.ndarray, forecast: np.ndarray) -> np.ndarray:
    """Per-period squared error, in squared vol points.

    Symmetric in levels: a 5-point miss at 15 vol costs the same as at 60 vol,
    so the few highest-vol days dominate it.
    """
    return (forecast - actual) ** 2


def qlike_loss(actual: np.ndarray, forecast: np.ndarray) -> np.ndarray:
    """Per-period QLIKE on variances: `s/f - log(s/f) - 1`.

    Scale-free — it sees the *ratio* of realized to forecast variance — and
    asymmetric: under-forecasting is punished far harder than over-forecasting,
    which is usually the right shape for volatility.

    This and squared error are robust in Patton's (2011) sense: the target is a
    noisy proxy for latent variance, and under a non-robust loss the ranking on
    the proxy differs from the ranking on the truth. MAE and correlation are
    not robust and must not be used to rank.
    """
    ratio = (actual / forecast) ** 2
    return ratio - np.log(ratio) - 1


LOSSES = {"mse": squared_error_loss, "qlike": qlike_loss}


def diebold_mariano_pair(
    actual: np.ndarray,
    forecast_a: np.ndarray,
    forecast_b: np.ndarray,
    loss: str,
    covariance: dict,
) -> dict:
    """Test whether forecast A is more accurate than forecast B.

    Form the per-period loss differential `d[t] = L(A) - L(B)` and test
    `E[d] = 0`, which is a regression of `d` on a constant with the caller's
    standard errors. A negative mean means A loses less, so `t < -1.96` says A
    beats B.

    Differencing is the point: both forecasts see the same shocks, the common
    component cancels, and what is left is the part of the accuracy gap that is
    not both models reacting to the same April. Plain DM assumes the two
    forecasts are non-nested; a model nested in its rival needs Clark-West,
    since DM is undersized there.
    """
    difference = LOSSES[loss](actual, forecast_a) - LOSSES[loss](actual, forecast_b)
    fit = sm.OLS(difference, np.ones(len(difference))).fit(**covariance)
    mean_difference, t_stat = float(fit.params[0]), float(fit.tvalues[0])
    return {
        "loss": loss,
        "mean_loss_diff": mean_difference,
        "t_stat": t_stat,
        "a_better": bool(t_stat < -1.96),
        "b_better": bool(t_stat > 1.96),
    }
