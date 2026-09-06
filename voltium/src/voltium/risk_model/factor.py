"""Factor risk model on per-unit-vega straddle P&L.

    Sigma = B F B' + D^2

`B` from a rolling `window`-day regression of each name's per-vega P&L on
the market factor and its own sector factor, `F` the Ledoit-Wolf-shrunk
sample covariance of the factor series over the same window, `D` the
residual standard deviation. All daily.

Factor construction (`build_factor_returns`):

* market: SPX reference straddle per-vega P&L if given, else the
  dollar-vega-weighted mean across the universe.
* sector s: the vega-weighted mean of its members' per-vega P&L, then
  regressed on the market over the full sample handed in and replaced by the
  residual, so sector loadings measure exposure beyond the market.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from voltium.risk_model.base import RiskModel
from voltium.risk_model.spec import MARKET, FactorSpec


def ledoit_wolf(sample: np.ndarray, n_obs: int) -> np.ndarray:
    """Ledoit-Wolf (2004) shrinkage toward the scaled identity.

    `sample` is the (k x k) sample covariance, `n_obs` the number of rows it
    came from. Uses the closed-form intensity from the original paper with
    the observations' fourth moments approximated by the sample covariance
    itself, which is the standard "well-conditioned estimator" recipe.
    """
    k = sample.shape[0]
    mu = np.trace(sample) / k
    target = mu * np.eye(k)
    delta = np.sum((sample - target) ** 2)
    if delta <= 0 or n_obs <= 1:
        return sample.copy()
    # beta^2 estimate: dispersion of the sample covariance around its mean over
    # observations; approximated as ||S||^2 / n, the usual plug-in
    beta = min(np.sum(sample**2) / n_obs, delta)
    intensity = beta / delta
    return intensity * target + (1.0 - intensity) * sample


def build_factor_returns(
    returns_df: pl.DataFrame,
    sectors_df: pl.DataFrame,
    spec: FactorSpec,
    market_df: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """`(date, factor, ret)` long frame.

    `returns_df` is `(date, symbol, pnl_per_vega, dollar_vega)`;
    `sectors_df` is `(symbol, sector)`; `market_df`, if given, is
    `(date, pnl_per_vega)` for the SPX reference straddle.
    """
    clean = returns_df.filter(pl.col("pnl_per_vega").is_not_null(), pl.col("dollar_vega") > 0)
    if spec.market_source == "spx" and market_df is not None:
        market = market_df.filter(pl.col("pnl_per_vega").is_not_null()).select(
            "date", pl.col("pnl_per_vega").alias("ret")
        )
    else:
        market = clean.group_by("date").agg(
            ((pl.col("pnl_per_vega") * pl.col("dollar_vega")).sum() / pl.col("dollar_vega").sum()).alias("ret")
        )
    market = market.with_columns(pl.lit(MARKET).alias("factor")).select("date", "factor", "ret")
    pieces = [market]

    if spec.use_sectors:
        raw = (
            clean.join(sectors_df, on="symbol", how="inner")
            .group_by("date", "sector")
            .agg(((pl.col("pnl_per_vega") * pl.col("dollar_vega")).sum() / pl.col("dollar_vega").sum()).alias("raw"))
            .join(market.select("date", pl.col("ret").alias("market")), on="date", how="inner")
        )
        # orthogonalise each sector to the market over the sample
        sector = (
            raw.with_columns(
                (pl.cov("raw", "market") / pl.col("market").var()).over("sector").alias("beta"),
                pl.col("raw").mean().over("sector").alias("raw_mean"),
                pl.col("market").mean().over("sector").alias("market_mean"),
            )
            .with_columns(
                (pl.col("raw") - pl.col("raw_mean") - pl.col("beta") * (pl.col("market") - pl.col("market_mean"))).alias("ret")
            )
            .select("date", pl.col("sector").alias("factor"), "ret")
        )
        pieces.append(sector)
    return pl.concat(pieces).sort("date", "factor")


class FactorRiskModel(RiskModel):
    def __init__(
        self,
        loadings_df: pl.DataFrame,
        factor_covariance: np.ndarray,
        factor_names: list[str],
        idio_df: pl.DataFrame,
    ) -> None:
        """`loadings_df`: (symbol, factor, loading); `idio_df`: (symbol, idio_vol)."""
        self.loadings_df = loadings_df
        self.factor_covariance = factor_covariance
        self.factor_names = list(factor_names)
        self.idio_df = idio_df
        self.symbol_list = sorted(set(loadings_df["symbol"]) & set(idio_df["symbol"]))
        self.loadings_wide_df = (
            loadings_df.pivot(index="symbol", on="factor", values="loading")
            .fill_null(0.0)
        )
        for name in self.factor_names:
            if name not in self.loadings_wide_df.columns:
                self.loadings_wide_df = self.loadings_wide_df.with_columns(pl.lit(0.0).alias(name))

    @property
    def symbols(self) -> list[str]:
        return self.symbol_list

    @property
    def factors(self) -> list[str]:
        return self.factor_names

    def loadings(self, symbols: list[str]) -> np.ndarray:
        aligned = pl.DataFrame({"symbol": symbols}).join(self.loadings_wide_df, on="symbol", how="left")
        return aligned.select(self.factor_names).fill_null(0.0).to_numpy()

    def idio_vol(self, symbols: list[str]) -> np.ndarray:
        aligned = pl.DataFrame({"symbol": symbols}).join(self.idio_df, on="symbol", how="left")
        missing = aligned.filter(pl.col("idio_vol").is_null())["symbol"].to_list()
        if missing:
            raise KeyError(f"no idio vol for {missing[:5]}{'...' if len(missing) > 5 else ''}")
        return aligned["idio_vol"].to_numpy()

    def covariance(self, symbols: list[str]) -> np.ndarray:
        b = self.loadings(symbols)
        d = self.idio_vol(symbols)
        sigma = b @ self.factor_covariance @ b.T + np.diag(d**2)
        return 0.5 * (sigma + sigma.T)


def estimate_factor_risk_model(
    returns_df: pl.DataFrame,
    factor_returns_df: pl.DataFrame,
    sectors_df: pl.DataFrame,
    spec: FactorSpec,
) -> FactorRiskModel:
    """Fit loadings, idio vol and shrunk factor covariance on the rows given.

    The caller is responsible for the window: hand in exactly the trailing
    `spec.window` sessions ending on the estimation date.
    """
    factors_wide = factor_returns_df.pivot(index="date", on="factor", values="ret").sort("date")
    factor_names = spec.factor_names([f for f in factors_wide.columns if f not in ("date", MARKET)])
    for name in factor_names:
        if name not in factors_wide.columns:
            factors_wide = factors_wide.with_columns(pl.lit(0.0).alias(name))
    factor_matrix = factors_wide.select(factor_names).fill_null(0.0).to_numpy()
    n_obs = factor_matrix.shape[0]
    sample = np.cov(factor_matrix, rowvar=False, ddof=1) if n_obs > 1 else np.zeros((len(factor_names),) * 2)
    sample = np.atleast_2d(sample)
    factor_cov = ledoit_wolf(sample, n_obs) if spec.shrinkage == "ledoit_wolf" else sample

    panel = (
        returns_df.filter(pl.col("pnl_per_vega").is_not_null())
        .join(sectors_df, on="symbol", how="left")
        .join(factors_wide, on="date", how="inner")
        .sort("symbol", "date")
    )
    loading_rows: list[dict] = []
    idio_rows: list[dict] = []
    for (symbol,), group in panel.group_by("symbol", maintain_order=True):
        if group.height < spec.min_observations:
            continue
        sector = group["sector"][0]
        columns = [MARKET]
        if spec.use_sectors and sector is not None and sector in factor_names:
            columns.append(sector)
        x = np.column_stack([np.ones(group.height), group.select(columns).to_numpy()])
        y = group["pnl_per_vega"].to_numpy()
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        residual = y - x @ beta
        for name, value in zip(columns, beta[1:]):
            loading_rows.append({"symbol": symbol, "factor": name, "loading": float(value)})
        idio_rows.append({"symbol": symbol, "idio_vol": float(np.std(residual, ddof=len(columns) + 1))})

    loadings_df = pl.DataFrame(loading_rows, schema={"symbol": pl.Utf8, "factor": pl.Utf8, "loading": pl.Float64})
    idio_df = pl.DataFrame(idio_rows, schema={"symbol": pl.Utf8, "idio_vol": pl.Float64})
    return FactorRiskModel(loadings_df, factor_cov, factor_names, idio_df)
