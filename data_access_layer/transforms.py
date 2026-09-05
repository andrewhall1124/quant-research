"""Derived quantities every study should compute the same way.

Expressions rather than functions over frames, so they compose into a
`with_columns` and can be partitioned with `.over("symbol")`.
"""

import polars as pl


def split_adjusted_return(
    close: str = "close", split_ratio: str = "split_ratio"
) -> pl.Expr:
    """Close-to-close simple return with the split applied on its ex-date.

    Only a split falling *between* the two closes matters to a return, so this
    needs the ex-date ratio rather than a cumulative back-adjustment factor —
    which also means a split after the sample ends is correctly irrelevant.
    Use over a symbol partition:

        prices_df.with_columns(split_adjusted_return().over("symbol"))
    """
    return (
        pl.col(close) * pl.col(split_ratio) / pl.col(close).shift(1) - 1
    ).alias("return")


def realized_volatility(
    close: pl.Series | pl.Expr, window: int, annualize: bool = True
) -> pl.Expr:
    """Rolling close-to-close realized vol from a price column.

    Kept here so every notebook computes RV the same way: standard deviation of
    log returns over `window` trading days, scaled by sqrt(252).
    """
    log_return = (pl.col(close) if isinstance(close, str) else close).log().diff()
    vol = log_return.rolling_std(window)
    return vol * (252**0.5) if annualize else vol
