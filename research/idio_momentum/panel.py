"""Residual reference returns: the reference straddle's P&L net of the risk model's factors.

    residual_t = pnl_per_vega_t - sum_f loading_{t-1,f} * factor_return_{t,f}

with the loadings from the stored risk model as of the previous session
(market plus own sector) and the stored factor returns, so the residual is
point in time. Written in `REFERENCE_SCHEMA` shape with `pnl_per_vega`
replaced by the residual, so `PastReturnSignal` reads it unchanged; a
second frame divides the residual by its trailing 252-session standard
deviation.

Run from the project root, after `research.vol_momentum.panel`:

    uv run python -m research.idio_momentum.panel
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from research.vol_momentum.panel import RESULTS_DIR as MOMENTUM_RESULTS
from voltium.loaders import DataPaths, scan_factor_loadings, scan_factor_returns

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"
SCALE_WINDOW = 252

log = logging.getLogger("idio_momentum")


def compute_residuals(reference_df: pl.DataFrame, loadings_df: pl.DataFrame, factor_returns_df: pl.DataFrame) -> pl.DataFrame:
    """`(date, symbol, pnl_per_vega, residual, explained)`; the loading used on day t is the one estimated through t-1."""
    sessions = reference_df.select("date").unique().sort("date").with_columns(pl.col("date").shift(-1).alias("next_date"))
    lagged = loadings_df.join(sessions, on="date").drop("date").rename({"next_date": "date"}).drop_nulls("date")
    explained = (
        lagged.join(factor_returns_df, on=["date", "factor"], how="inner")
        .group_by("date", "symbol")
        .agg((pl.col("loading") * pl.col("ret")).sum().alias("explained"))
    )
    return (
        reference_df.join(explained, on=["date", "symbol"], how="inner")
        .with_columns((pl.col("pnl_per_vega") - pl.col("explained")).alias("residual"))
        .select("date", "symbol", "pnl_per_vega", "residual", "explained")
        .sort("symbol", "date")
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    RESULTS_DIR.mkdir(exist_ok=True)
    paths = DataPaths.from_repo()
    reference_df = pl.read_parquet(MOMENTUM_RESULTS / "reference.parquet")
    residuals_df = compute_residuals(reference_df, scan_factor_loadings(paths).collect(), scan_factor_returns(paths).collect())
    share = residuals_df.select((pl.col("explained").var() / pl.col("pnl_per_vega").var())).item()
    log.info("%d rows; factors explain %.1f%% of reference-return variance", residuals_df.height, 100 * share)
    residual_panel = residuals_df.select("date", "symbol", pl.col("residual").alias("pnl_per_vega"))
    scaled_panel = residuals_df.with_columns(
        (pl.col("residual") / pl.col("residual").rolling_std(SCALE_WINDOW, min_samples=SCALE_WINDOW // 2).over("symbol")).alias("pnl_per_vega")
    ).select("date", "symbol", "pnl_per_vega")
    residual_panel.write_parquet(RESULTS_DIR / "residual_reference.parquet")
    scaled_panel.write_parquet(RESULTS_DIR / "scaled_reference.parquet")
    residuals_df.write_parquet(RESULTS_DIR / "residuals.parquet")
    log.info("written to %s", RESULTS_DIR)


if __name__ == "__main__":
    main()
