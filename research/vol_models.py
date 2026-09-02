"""The volatility forecasters, shared by every study in `research/`.

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


# A window with too many blanked returns is not a volatility estimate.
MIN_VALID_SHARE = 0.8


def realized_vol(returns: np.ndarray) -> float:
    """Annualized realized volatility of a block of log returns.

    NaN-aware, because single-name return series carry holes where a split or
    an unverifiable jump was blanked out. A window that has lost more than
    `1 - MIN_VALID_SHARE` of its days returns NaN rather than a number computed
    off whatever is left.
    """
    valid = returns[np.isfinite(returns)]
    if len(returns) == 0 or len(valid) < MIN_VALID_SHARE * len(returns):
        return float("nan")
    return float(np.sqrt(TRADING_DAYS * np.mean(valid**2)))


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
    return fit_and_forecast_horizons(returns, [horizon], model, burn_in, refit_every)[horizon]


def fit_and_forecast_horizons(
    returns: np.ndarray,
    horizons: list[int],
    model: str,
    burn_in: int,
    refit_every: int = 5,
) -> dict[int, np.ndarray]:
    """`fit_and_forecast` for several horizons off one pass.

    The conditional-variance path a model projects at an origin already covers
    every horizon, so fitting once per origin and slicing the path is exactly
    equivalent to fitting per horizon — and twice as fast, which matters when
    the sweep runs over 500 names rather than one index.
    """
    if model not in {"ARCH", "GARCH"}:
        raise ValueError(f"unknown model {model!r}")

    # arch's optimizer wants returns on a percent scale. NaNs left by a
    # corporate action are dropped for estimation; the index is not used.
    
    scaled = pd.Series(returns * 100)
    out = {horizon: np.full(len(returns), np.nan) for horizon in horizons}
    longest = max(horizons)
    params = None

    for origin in range(burn_in, len(returns)):
        window = scaled.iloc[: origin + 1].dropna()
        specification = (
            arch_model(window, mean="Constant", vol="ARCH", p=5)
            if model == "ARCH"
            else arch_model(window, mean="Constant", vol="GARCH", p=1, q=1)
        )

        if params is None or (origin - burn_in) % refit_every == 0:
            params = specification.fit(disp="off", show_warning=False).params

        fixed = specification.fix(params)
        path = fixed.forecast(horizon=longest, reindex=False).variance.values[-1]
        for horizon in horizons:
            # Mean variance per day over the horizon, back from percent to decimal.
            mean_variance = float(np.mean(path[:horizon])) / 10_000
            out[horizon][origin] = np.sqrt(TRADING_DAYS * mean_variance)

    return out
