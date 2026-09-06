"""Per-name features from stock returns, used by the signal and the optimizer.

    beta        250-session OLS beta of the stock's log return on the market
    idio_vol    60-session residual vol of that regression, annualised
    log_size    log of the 60-session mean dollar volume (close x volume)
    gap_freq    share of the trailing 250 sessions with |return| > 3 sigma,
                sigma being the trailing 60-session return std

**`log_size` is a proxy.** The store carries no shares outstanding or market
capitalisation, so the size control in the signal regression is average
dollar volume rather than log market cap. It is flagged in the README; swap
in a market-cap provider here when one exists.

The market return defaults to the SPX close from the index chain's
`underlying` column, which reaches the whole option history; the stock file
only starts mid-2023.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import polars as pl

from voltium.loaders import DataPaths, scan_options, scan_stocks
from voltium.providers.base import PanelProvider

SESSIONS_PER_YEAR = 252


@dataclass(frozen=True)
class StockFeatureConfig:
    beta_window: int = 250
    idio_window: int = 60
    size_window: int = 60
    gap_window: int = 250
    gap_sigma_window: int = 60
    gap_threshold: float = 3.0


def load_spx_closes(paths: DataPaths, start: dt.date | None, end: dt.date | None) -> pl.DataFrame:
    """`(date, market_return)` from the SPX chain's underlying price."""
    closes = (
        scan_options(paths, ["SPX"], start, end, index=True, with_oi=False)
        .select("date", "underlying")
        .unique(subset=["date"])
        .sort("date")
        .collect()
    )
    return closes.with_columns((pl.col("underlying") / pl.col("underlying").shift(1)).log().alias("market_return")).select(
        "date", "market_return"
    )


def compute_stock_features(
    stocks: pl.LazyFrame, market_df: pl.DataFrame, config: StockFeatureConfig = StockFeatureConfig()
) -> pl.LazyFrame:
    by = "symbol"
    if "split_ratio" not in stocks.collect_schema().names():
        stocks = stocks.with_columns(pl.lit(1.0).alias("split_ratio"))
    frame = (
        stocks.sort("symbol", "date")
        .with_columns((pl.col("close") * pl.col("split_ratio") / pl.col("close").shift(1).over(by)).log().alias("ret"))
        .join(market_df.lazy(), on="date", how="inner")
        .sort("symbol", "date")
    )
    cov = pl.rolling_cov("ret", "market_return", window_size=config.beta_window).over(by)
    var_m = pl.col("market_return").rolling_var(config.beta_window).over(by)
    beta = (cov / var_m).alias("beta")
    frame = frame.with_columns(beta)

    residual = (pl.col("ret") - pl.col("beta") * pl.col("market_return")).alias("residual")
    frame = frame.with_columns(residual).with_columns(
        (pl.col("residual").rolling_std(config.idio_window).over(by) * SESSIONS_PER_YEAR**0.5).alias("idio_vol"),
        (pl.col("close") * pl.col("volume")).rolling_mean(config.size_window).over(by).log().alias("log_size"),
        pl.col("ret").rolling_std(config.gap_sigma_window).over(by).alias("sigma"),
    )
    gap = (pl.col("ret").abs() > config.gap_threshold * pl.col("sigma").shift(1).over(by)).cast(pl.Float64)
    frame = frame.with_columns(gap.rolling_mean(config.gap_window).over(by).alias("gap_freq"))
    return frame.select("date", "symbol", "ret", "beta", "idio_vol", "log_size", "gap_freq")


class StockFeaturesProvider(PanelProvider):
    """`get(date) -> (date, symbol, ret, beta, idio_vol, log_size, gap_freq)`."""

    @classmethod
    def from_store(
        cls,
        paths: DataPaths,
        symbols: list[str] | None,
        start: dt.date | None,
        end: dt.date | None,
        config: StockFeatureConfig = StockFeatureConfig(),
    ) -> "StockFeaturesProvider":
        stocks = scan_stocks(paths, start, end, with_splits=True)
        if symbols is not None:
            stocks = stocks.filter(pl.col("symbol").is_in(symbols))
        market_df = load_spx_closes(paths, start, end)
        return cls(compute_stock_features(stocks, market_df, config).collect())
