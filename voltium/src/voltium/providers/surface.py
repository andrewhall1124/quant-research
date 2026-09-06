"""Constant-maturity ATM implied vol per (date, symbol).

For each (date, symbol, expiration): the call and the put nearest |delta| =
0.50 with a usable quote, their mid IVs averaged -> the expiration's ATM IV.
Those pillars are converted to total variance and linearly interpolated to
the configured tenors (30/60/90 calendar days by default).

The `EarningsAdjuster` hook sits between the pillars and the interpolation:
that is where an earnings jump variance would be subtracted from every
expiration that spans the announcement, so the constant-maturity IV is a
"clean" diffusion vol. v1 ships only the no-op.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass

import polars as pl

from voltium.loaders import DataPaths, scan_options
from voltium.providers.base import PanelProvider, Provider
from voltium.utils.interpolation import build_tenor_columns


@dataclass(frozen=True)
class SurfaceConfig:
    tenors: tuple[int, ...] = (30, 60, 90)
    min_dte: int = 7
    max_dte: int = 400
    delta_band: tuple[float, float] = (0.25, 0.75)
    require_both_rights: bool = True
    extrapolate: bool = False


class EarningsAdjuster(ABC):
    """Hook: transform the per-expiration ATM pillars before interpolation.

    Input and output have columns `(date, symbol, expiration, dte, atm_iv)`.
    A real implementation would join the next announcement date, and for every
    expiration after it replace `atm_iv` with
    `sqrt(atm_iv^2 - jump_var * 365 / dte)`.
    """

    @abstractmethod
    def adjust(self, pillars: pl.LazyFrame) -> pl.LazyFrame: ...


class NoOpEarningsAdjuster(EarningsAdjuster):
    def adjust(self, pillars: pl.LazyFrame) -> pl.LazyFrame:
        return pillars


def compute_atm_pillars(options: pl.LazyFrame, config: SurfaceConfig) -> pl.LazyFrame:
    """`(date, symbol, expiration, dte, atm_iv, underlying)` from canonical rows.

    Nearest-to-0.50 |delta| per right, then the average across rights.
    """
    distance = (pl.col("delta").abs() - 0.5).abs()
    candidates = (
        options.filter(
            pl.col("iv").is_not_null(),
            pl.col("bid") > 0,
            pl.col("ask") > pl.col("bid"),
            pl.col("delta").abs().is_between(*config.delta_band),
        )
        .with_columns((pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"))
        .filter(pl.col("dte").is_between(config.min_dte, config.max_dte))
        .with_columns(distance.alias("distance"))
    )
    best_per_right = (
        candidates.sort("distance", "strike")  # strike breaks a tie deterministically
        .group_by("date", "symbol", "expiration", "right", maintain_order=True)
        .agg(pl.col("iv").first(), pl.col("dte").first(), pl.col("underlying").first())
    )
    pillars = best_per_right.group_by("date", "symbol", "expiration").agg(
        pl.col("iv").mean().alias("atm_iv"),
        pl.col("dte").first(),
        pl.col("underlying").first(),
        pl.col("right").n_unique().alias("rights"),
    )
    if config.require_both_rights:
        pillars = pillars.filter(pl.col("rights") == 2)
    return pillars.drop("rights")


def build_surface_panel(
    options: pl.LazyFrame,
    config: SurfaceConfig = SurfaceConfig(),
    earnings_adjuster: EarningsAdjuster | None = None,
) -> pl.LazyFrame:
    """`(date, symbol, iv30, iv60, iv90, ...)` lazily, from canonical rows."""
    adjuster = earnings_adjuster or NoOpEarningsAdjuster()
    pillars = adjuster.adjust(compute_atm_pillars(options, config))
    return build_tenor_columns(
        pillars.select("date", "symbol", "dte", "atm_iv"),
        group_by=["date", "symbol"],
        dte_col="dte",
        iv_col="atm_iv",
        target_dtes=config.tenors,
        extrapolate=config.extrapolate,
    ).sort("date", "symbol")


class SurfaceProvider(Provider):
    """`get(date) -> (date, symbol, iv30, iv60, iv90)` (columns per `tenors`)."""


class ConstantMaturitySurface(SurfaceProvider, PanelProvider):
    def __init__(self, panel_df: pl.DataFrame, config: SurfaceConfig = SurfaceConfig()) -> None:
        self.config = config
        PanelProvider.__init__(self, panel_df)

    @classmethod
    def from_store(
        cls,
        paths: DataPaths,
        symbols: list[str],
        start: dt.date,
        end: dt.date,
        config: SurfaceConfig = SurfaceConfig(),
        earnings_adjuster: EarningsAdjuster | None = None,
        index: bool = False,
    ) -> "ConstantMaturitySurface":
        options = scan_options(paths, symbols, start, end, index=index, with_oi=False)
        panel_df = build_surface_panel(options, config, earnings_adjuster).collect(engine="streaming")
        return cls(panel_df, config)
