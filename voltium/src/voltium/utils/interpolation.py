"""Constant-maturity implied vol by linear interpolation in total variance.

Total variance `w = iv^2 * t` (t in years) is linear in time under a flat
forward-variance assumption, which is the standard way to read an IV surface
at a tenor no listed expiration sits on. Interpolating IV directly would put
a kink at every expiration.

Everything here is a Polars expression or group-wise function so it composes
into a lazy pipeline; the only eager step is the per-group interpolation,
which runs on a handful of expirations per (date, symbol).
"""

from __future__ import annotations

import numpy as np
import polars as pl

DAYS_PER_YEAR = 365.0


def interpolate_total_variance(
    dte: np.ndarray, iv: np.ndarray, target_dte: float, extrapolate: bool = False
) -> float:
    """IV at `target_dte` calendar days from (dte, iv) pillars of one surface.

    Pillars are sorted here; duplicates in `dte` are averaged. Returns nan if
    fewer than one pillar, or if the target is outside the pillar range and
    `extrapolate` is False. With one pillar the IV is held flat.

    With `extrapolate=True`, a target outside the range uses the nearest
    pillar's IV held flat (flat-vol extrapolation), never a slope. Term
    structures invert; extrapolating a slope out of the last two pillars gives
    negative variance too easily to be worth it.
    """
    dte = np.asarray(dte, dtype=float)
    iv = np.asarray(iv, dtype=float)
    keep = np.isfinite(dte) & np.isfinite(iv) & (dte > 0) & (iv > 0)
    dte, iv = dte[keep], iv[keep]
    if dte.size == 0:
        return float("nan")
    order = np.argsort(dte)
    dte, iv = dte[order], iv[order]
    unique_dte, inverse = np.unique(dte, return_inverse=True)
    if unique_dte.size != dte.size:
        iv = np.array([iv[inverse == i].mean() for i in range(unique_dte.size)])
        dte = unique_dte
    if dte.size == 1:
        return float(iv[0]) if extrapolate or dte[0] == target_dte else float("nan")
    if target_dte < dte[0] or target_dte > dte[-1]:
        if not extrapolate:
            return float("nan")
        return float(iv[0]) if target_dte < dte[0] else float(iv[-1])
    tau = dte / DAYS_PER_YEAR
    total_variance = iv**2 * tau
    target_tau = target_dte / DAYS_PER_YEAR
    w = np.interp(target_tau, tau, total_variance)
    return float(np.sqrt(max(w, 0.0) / target_tau))


def interpolate_tenors(
    dte: np.ndarray, iv: np.ndarray, target_dtes: tuple[int, ...], extrapolate: bool = False
) -> list[float]:
    return [interpolate_total_variance(dte, iv, t, extrapolate) for t in target_dtes]


def build_tenor_columns(
    frame: pl.LazyFrame,
    group_by: list[str],
    dte_col: str,
    iv_col: str,
    target_dtes: tuple[int, ...],
    extrapolate: bool = False,
) -> pl.LazyFrame:
    """Collapse rows per group into one row with an `iv{dte}` column per tenor.

    Runs the interpolation inside a `map_groups`-free aggregation: pillars are
    gathered into list columns, and the per-row interpolation is applied with
    `map_elements` over the struct. This keeps the frame lazy up to the point
    the caller collects.
    """
    names = [f"iv{t}" for t in target_dtes]

    def interpolate_row(row: dict) -> dict:
        values = interpolate_tenors(
            np.asarray(row[dte_col], dtype=float),
            np.asarray(row[iv_col], dtype=float),
            target_dtes,
            extrapolate,
        )
        return dict(zip(names, values))

    return (
        frame.group_by(group_by)
        .agg(pl.col(dte_col), pl.col(iv_col))
        .with_columns(
            pl.struct(dte_col, iv_col)
            .map_elements(
                interpolate_row,
                return_dtype=pl.Struct({name: pl.Float64 for name in names}),
            )
            .alias("tenors")
        )
        .unnest("tenors")
        .drop(dte_col, iv_col)
        .with_columns([pl.col(name).fill_nan(None) for name in names])
    )
