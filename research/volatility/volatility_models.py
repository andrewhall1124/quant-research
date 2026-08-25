"""The four volatility forecasters compared in this study.

Every model answers the same question and returns the same thing: given
information through day `t` only, what is annualized volatility going to be
over days `t+1 .. t+h`, as a decimal (0.18 = 18%)?

Volatility is defined the zero-mean way throughout,

    vol = sqrt(252 * mean(r^2))

so that the target, the trailing-RV predictor and the GARCH variance forecast
are all the same object. Using a demeaned standard deviation instead moves
every number by well under a basis point at these horizons, but mixing the two
conventions would quietly bias the comparison.
"""

import numpy as np
import pandas as pd
from arch import arch_model

TRADING_DAYS = 252

# Conditional-variance specifications, as keyword arguments to `arch_model`.
SPECIFICATIONS = {
    "ARCH": {"vol": "ARCH", "p": 5},
    "GARCH": {"vol": "GARCH", "p": 1, "q": 1},
    "GJR": {"vol": "GARCH", "p": 1, "o": 1, "q": 1},
}


def realized_vol(returns: np.ndarray) -> float:
    """Annualized realized volatility of a block of log returns."""
    return float(np.sqrt(TRADING_DAYS * np.mean(returns**2)))


def forward_realized_vol(returns: np.ndarray, horizon: int) -> np.ndarray:
    """`out[t]` = realized vol over `t+1 .. t+horizon`; NaN where it runs off the end."""
    out = np.full(len(returns), np.nan)
    for t in range(len(returns) - horizon):
        out[t] = realized_vol(returns[t + 1 : t + 1 + horizon])
    return out


def trailing_realized_vol(returns: np.ndarray, window: int) -> np.ndarray:
    """`out[t]` = realized vol over the `window` days ending at `t` (inclusive)."""
    out = np.full(len(returns), np.nan)
    for t in range(window - 1, len(returns)):
        out[t] = realized_vol(returns[t + 1 - window : t + 1])
    return out


def ewma_vol(returns: np.ndarray, decay: float = 0.94) -> np.ndarray:
    """RiskMetrics EWMA variance, as an annualized vol path.

    `out[t]` uses returns through `t`. The h-step forecast is this level held
    flat: EWMA is IGARCH(1,1) with frozen parameters, so it has no mean to
    revert to and its term structure is a horizontal line.
    """
    variance = np.full(len(returns), np.nan)
    running = float(np.var(returns[:22]))
    for t in range(len(returns)):
        running = decay * running + (1 - decay) * returns[t] ** 2
        variance[t] = running
    return np.sqrt(TRADING_DAYS * variance)


def har_features(returns: np.ndarray) -> np.ndarray:
    """Corsi's three trailing horizons — daily, weekly, monthly RV — per day."""
    return np.column_stack(
        [
            trailing_realized_vol(returns, 1),
            trailing_realized_vol(returns, 5),
            trailing_realized_vol(returns, 22),
        ]
    )


def fit_regression_forecast(
    features: np.ndarray,
    target: np.ndarray,
    horizon: int,
    burn_in: int,
    return_coefficients: bool = False,
) -> np.ndarray:
    """Expanding-window OLS forecasts, used for both HAR and premium-adjusted IV.

    This is the one family in the study that is fitted *to the target*, which
    is what lets it be correctly scaled where the unfitted models are not. The
    fairness constraint is that at origin `t` the training set may only contain
    observations whose target had already been realized by `t` — that is,
    origins `s` with `s + horizon <= t`. Forgetting that leaks the future and
    flatters HAR enormously.
    """
    design = np.column_stack([np.ones(len(target)), features])
    out = np.full(len(target), np.nan)
    path = np.full((len(target), design.shape[1]), np.nan)

    for origin in range(burn_in, len(target)):
        usable = np.arange(len(target)) + horizon <= origin
        usable &= np.isfinite(target) & np.isfinite(design).all(axis=1)
        if usable.sum() < 30 or not np.isfinite(design[origin]).all():
            continue
        coefficients, *_ = np.linalg.lstsq(design[usable], target[usable], rcond=None)
        path[origin] = coefficients
        out[origin] = float(design[origin] @ coefficients)

    # A vol forecast below zero is a regression artifact, not a view.
    clipped = np.clip(out, 0.01, None)
    return (clipped, path) if return_coefficients else clipped


# --- Range-based estimators -------------------------------------------------
#
# All three return a *daily variance* series. They use the high-low range
# rather than one close-to-close return, which is why they are 4-8x more
# efficient per day than squared returns: a single close throws away everything
# the price did in between.


def parkinson_variance(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """High-low only. Immune to the opening print, blind to overnight moves."""
    return (np.log(high / low) ** 2) / (4 * np.log(2))


def garman_klass_variance(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """Adds the open-to-close move; more efficient, but leans on the open."""
    log_range = np.log(high / low)
    log_move = np.log(close / open_)
    return 0.5 * log_range**2 - (2 * np.log(2) - 1) * log_move**2


def rogers_satchell_variance(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """Drift-independent, so it does not mistake a trending day for a volatile one."""
    return np.log(high / close) * np.log(high / open_) + np.log(low / close) * np.log(
        low / open_
    )


def garman_klass_yang_zhang_variance(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """Garman-Klass plus the overnight gap — the one range estimator that is
    scale-comparable to close-to-close RV, because it is the only one that sees
    the move between yesterday's close and today's open.

    The three intraday-only estimators above run 3+ vol points below
    close-to-close on SPX for exactly that reason, so they cannot be swapped in
    as a target without changing what "volatility" means.
    """
    overnight = np.concatenate([[np.nan], np.log(open_[1:] / close[:-1])])
    return overnight**2 + garman_klass_variance(open_, high, low, close)


def trailing_range_vol(daily_variance: np.ndarray, window: int) -> np.ndarray:
    """Annualized vol from the mean of a daily-variance series over `window` days."""
    out = np.full(len(daily_variance), np.nan)
    for t in range(window - 1, len(daily_variance)):
        block = daily_variance[t + 1 - window : t + 1]
        if np.isfinite(block).all():
            out[t] = np.sqrt(TRADING_DAYS * np.mean(block))
    return out


def forward_range_vol(daily_variance: np.ndarray, horizon: int) -> np.ndarray:
    """The range-based analogue of `forward_realized_vol`, for the robustness target."""
    out = np.full(len(daily_variance), np.nan)
    for t in range(len(daily_variance) - horizon):
        block = daily_variance[t + 1 : t + 1 + horizon]
        if np.isfinite(block).all():
            out[t] = np.sqrt(TRADING_DAYS * np.mean(block))
    return out


def fit_and_forecast(
    returns: np.ndarray,
    horizon: int,
    model: str,
    burn_in: int,
    refit_every: int = 5,
) -> np.ndarray:
    """Rolling out-of-sample forecasts from a conditional-variance model.

    At each origin `t >= burn_in` the model sees returns up to and including
    `t` — an expanding window, never the future. Parameters are re-estimated
    every `refit_every` origins and held fixed in between (the likelihood moves
    very little day to day, and this keeps the sweep to seconds); the
    conditional variance itself still updates daily off the new observation.

    `model` is "ARCH" (ARCH(5)), "GARCH" (GARCH(1,1)) or "GJR"
    (GJR-GARCH(1,1,1), which lets a down day raise variance more than an up day
    of the same size).
    """
    if model not in SPECIFICATIONS:
        raise ValueError(f"unknown model {model!r}")

    # arch's optimizer wants returns on a percent scale.
    scaled = pd.Series(returns * 100)
    out = np.full(len(returns), np.nan)
    params = None

    for origin in range(burn_in, len(returns)):
        window = scaled.iloc[: origin + 1]
        specification = arch_model(window, mean="Constant", **SPECIFICATIONS[model])

        if params is None or (origin - burn_in) % refit_every == 0:
            params = specification.fit(disp="off", show_warning=False).params

        fixed = specification.fix(params)
        forecast = fixed.forecast(horizon=horizon, reindex=False)
        # Mean variance per day over the horizon, back from percent to decimal.
        mean_variance = float(np.mean(forecast.variance.values[-1])) / 10_000
        out[origin] = np.sqrt(TRADING_DAYS * mean_variance)

    return out
