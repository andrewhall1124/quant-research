"""GICS sector for each current S&P 500 constituent, from Wikipedia.

The store has point-in-time membership but no classification, and a
sector-neutral volatility book needs one. Wikipedia's constituent table
carries the GICS sector and sub-industry of *today's* members only, so this
is a static snapshot: a name that left the index is missing and a name that
changed sector is stamped with its current one. See voltium's README for
what that costs.

    uv run python -m data_pipelines.cli sectors
"""

import argparse

import polars as pl
from dotenv import load_dotenv

from data_access_layer import paths
from data_pipelines.universe_pipeline import CURRENT_URL, fetch_first_table
from data_pipelines.utils import normalize_ticker, write_atomic

load_dotenv()


def fetch_sectors() -> pl.DataFrame:
    current_df = fetch_first_table(CURRENT_URL)
    return (
        pl.from_pandas(current_df)
        .select(
            pl.col("Symbol").alias("ticker"),
            pl.col("GICS Sector").alias("sector"),
            pl.col("GICS Sub-Industry").alias("sub_industry"),
        )
        .drop_nulls("ticker")
        .with_columns(
            pl.col("ticker")
            .map_elements(lambda t: normalize_ticker(t, "option"), return_dtype=pl.String)
            .alias("symbol")
        )
        .select("ticker", "symbol", "sector", "sub_industry")
        .sort("ticker")
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def main(args: argparse.Namespace) -> None:
    sectors_df = fetch_sectors()
    write_atomic(sectors_df, paths.SECTORS)
    print(f"wrote {paths.SECTORS}: {sectors_df.height} rows")
    print(sectors_df.group_by("sector").len().sort("sector"))
