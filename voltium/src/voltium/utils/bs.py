"""Black-Scholes prices and greeks, vectorised over numpy arrays.

Used for two things only: the vega-convention check in `loaders.py`, and
marking a leg whose quote is missing on a given day. The vendor greeks are the
primary source everywhere else.

Conventions (chosen to match the canonical schema in `loaders.py`):

* `sigma` and `rate` are decimals (0.25 = 25% vol, 0.05 = 5%).
* `tau` is in years (calendar days / 365).
* `vega` is per share per 1.00 of sigma; divide by 100 for a vol point.
* `theta` is per share per calendar day.
"""

from __future__ import annotations

import numpy as np
from scipy.special import ndtr

DAYS_PER_YEAR = 365.0


def compute_d1_d2(
    spot: np.ndarray, strike: np.ndarray, tau: np.ndarray, rate: np.ndarray, sigma: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    tau = np.maximum(np.asarray(tau, dtype=float), 1e-12)
    sigma = np.maximum(np.asarray(sigma, dtype=float), 1e-12)
    root_tau = np.sqrt(tau)
    d1 = (np.log(spot / strike) + (rate + 0.5 * sigma**2) * tau) / (sigma * root_tau)
    d2 = d1 - sigma * root_tau
    return d1, d2


def price(
    spot: np.ndarray,
    strike: np.ndarray,
    tau: np.ndarray,
    rate: np.ndarray,
    sigma: np.ndarray,
    is_call: np.ndarray,
) -> np.ndarray:
    """European option price. `is_call` is a boolean array (True for calls)."""
    d1, d2 = compute_d1_d2(spot, strike, tau, rate, sigma)
    discount = np.exp(-rate * tau)
    call = spot * ndtr(d1) - strike * discount * ndtr(d2)
    put = strike * discount * ndtr(-d2) - spot * ndtr(-d1)
    return np.where(is_call, call, put)


def delta(
    spot: np.ndarray,
    strike: np.ndarray,
    tau: np.ndarray,
    rate: np.ndarray,
    sigma: np.ndarray,
    is_call: np.ndarray,
) -> np.ndarray:
    d1, _ = compute_d1_d2(spot, strike, tau, rate, sigma)
    return np.where(is_call, ndtr(d1), ndtr(d1) - 1.0)


def gamma(
    spot: np.ndarray, strike: np.ndarray, tau: np.ndarray, rate: np.ndarray, sigma: np.ndarray
) -> np.ndarray:
    d1, _ = compute_d1_d2(spot, strike, tau, rate, sigma)
    tau = np.maximum(np.asarray(tau, dtype=float), 1e-12)
    return normal_pdf(d1) / (spot * sigma * np.sqrt(tau))


def vega(
    spot: np.ndarray, strike: np.ndarray, tau: np.ndarray, rate: np.ndarray, sigma: np.ndarray
) -> np.ndarray:
    """Per share, per 1.00 change in sigma."""
    d1, _ = compute_d1_d2(spot, strike, tau, rate, sigma)
    tau = np.maximum(np.asarray(tau, dtype=float), 1e-12)
    return spot * normal_pdf(d1) * np.sqrt(tau)


def theta(
    spot: np.ndarray,
    strike: np.ndarray,
    tau: np.ndarray,
    rate: np.ndarray,
    sigma: np.ndarray,
    is_call: np.ndarray,
) -> np.ndarray:
    """Per share, per calendar day."""
    d1, d2 = compute_d1_d2(spot, strike, tau, rate, sigma)
    tau = np.maximum(np.asarray(tau, dtype=float), 1e-12)
    discount = np.exp(-rate * tau)
    common = -spot * normal_pdf(d1) * sigma / (2.0 * np.sqrt(tau))
    call = common - rate * strike * discount * ndtr(d2)
    put = common + rate * strike * discount * ndtr(-d2)
    return np.where(is_call, call, put) / DAYS_PER_YEAR


def normal_pdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * np.square(x)) / np.sqrt(2.0 * np.pi)


def implied_vol(
    target: np.ndarray,
    spot: np.ndarray,
    strike: np.ndarray,
    tau: np.ndarray,
    rate: np.ndarray,
    is_call: np.ndarray,
    iterations: int = 60,
) -> np.ndarray:
    """Bisection on sigma in [1e-4, 5.0]. Returns nan where no root exists."""
    low = np.full_like(np.asarray(target, dtype=float), 1e-4)
    high = np.full_like(low, 5.0)
    for _ in range(iterations):
        mid = 0.5 * (low + high)
        too_high = price(spot, strike, tau, rate, mid, is_call) > target
        high = np.where(too_high, mid, high)
        low = np.where(too_high, low, mid)
    sigma = 0.5 * (low + high)
    lower_bound = price(spot, strike, tau, rate, low * 0 + 1e-4, is_call)
    upper_bound = price(spot, strike, tau, rate, low * 0 + 5.0, is_call)
    valid = (target >= lower_bound) & (target <= upper_bound)
    return np.where(valid, sigma, np.nan)
