"""Pull benchmark rate series from FRED.

Two families, for different jobs:

*Treasury constant maturities* (DGS*) give a proper short end -- 1m, 3m, 6m, 1y,
2y -- which the CBOE yield indices could not, having only a 13-week point before
jumping to 5 years. Most measurable boxes live at 6-12 months, exactly the gap
the CBOE curve had to interpolate across.

*SOFR averages* are the compounded realised overnight rate over trailing 30/90/180
day windows. They are backward-looking, so they are not the forward OIS rate a box
prices; they are the closest free proxy for the rate a box's horizon averaged over.
Treat them as a sanity check on level, not as the true benchmark.
"""

import io
from pathlib import Path

import polars as pl
import requests

SERIES = {
    "DGS1MO": ("treasury", 1 / 12),
    "DGS3MO": ("treasury", 0.25),
    "DGS6MO": ("treasury", 0.5),
    "DGS1": ("treasury", 1.0),
    "DGS2": ("treasury", 2.0),
    "SOFR": ("sofr", 0.0),
    "SOFR30DAYAVG": ("sofr_avg", 30 / 365),
    "SOFR90DAYAVG": ("sofr_avg", 90 / 365),
    "SOFR180DAYAVG": ("sofr_avg", 180 / 365),
}

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def fetch(series_id: str) -> pl.DataFrame:
    response = requests.get(
        URL, params={"id": series_id, "cosd": "2024-12-01", "coed": "2025-12-31"},
        timeout=60,
    )
    response.raise_for_status()
    raw = pl.read_csv(io.StringIO(response.text), null_values=["."])
    # FRED renames the value column to the series id; the date column varies in case.
    date_col, value_col = raw.columns[0], raw.columns[1]
    family, tenor = SERIES[series_id]
    return (
        raw.select(
            pl.col(date_col).str.strptime(pl.Date, "%Y-%m-%d").alias("date"),
            pl.col(value_col).cast(pl.Float64, strict=False).alias("pct"),
        )
        .drop_nulls()
        .with_columns(
            pl.lit(series_id).alias("series"),
            pl.lit(family).alias("family"),
            pl.lit(tenor).alias("tenor_y"),
            (pl.col("pct") / 100).alias("rate"),
        )
        .drop("pct")
    )


if __name__ == "__main__":
    frames = []
    for series_id in SERIES:
        df = fetch(series_id)
        print(f"  {series_id:<14} {df.height:>4} obs  "
              f"{df['rate'].min() * 100:.2f}% .. {df['rate'].max() * 100:.2f}%")
        frames.append(df)

    out = pl.concat(frames, how="vertical_relaxed").sort("series", "date")
    Path("data").mkdir(exist_ok=True)
    out.write_parquet("data/fred_rates.parquet")
    print(f"\nwrote data/fred_rates.parquet  ({out.height:,} rows)")
