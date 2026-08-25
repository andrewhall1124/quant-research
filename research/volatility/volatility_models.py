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

    `model` is "ARCH" (ARCH(5)) or "GARCH" (GARCH(1,1)).
    """
    if model not in {"ARCH", "GARCH"}:
        raise ValueError(f"unknown model {model!r}")

    # arch's optimizer wants returns on a percent scale.
    scaled = pd.Series(returns * 100)
    out = np.full(len(returns), np.nan)
    params = None

    for origin in range(burn_in, len(returns)):
        window = scaled.iloc[: origin + 1]
        specification = (
            arch_model(window, mean="Constant", vol="ARCH", p=5)
            if model == "ARCH"
            else arch_model(window, mean="Constant", vol="GARCH", p=1, q=1)
        )

        if params is None or (origin - burn_in) % refit_every == 0:
            params = specification.fit(disp="off", show_warning=False).params

        fixed = specification.fix(params)
        forecast = fixed.forecast(horizon=horizon, reindex=False)
        # Mean variance per day over the horizon, back from percent to decimal.
        mean_variance = float(np.mean(forecast.variance.values[-1])) / 10_000
        out[origin] = np.sqrt(TRADING_DAYS * mean_variance)

    return out
