"""Compare box-implied rates to a term-matched Treasury yield, not overnight SOFR.

A 9-month box prices 9-month money. Holding it against overnight SOFR conflates
the level of rates with the slope of the curve, which in an easing year is a
large effect. The CBOE yield indices give four curve points (13w, 5y, 10y, 30y);
this interpolates them linearly in maturity to each box's own T.
"""

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from implied_rates import DAYS_PER_YEAR, box_rates, build_synthetics
from run_analysis import prepare

TENOR_YEARS = {"13w": 0.25, "5y": 5.0, "10y": 10.0, "30y": 30.0}


def interpolate_curve(yields_df: pl.DataFrame) -> pl.DataFrame:
    """Wide curve per date, with tenors as years."""
    return (
        yields_df.with_columns(pl.col("tenor").replace_strict(TENOR_YEARS).alias("tenor_y"))
        .sort("date", "tenor_y")
        .group_by("date")
        .agg(pl.col("tenor_y"), pl.col("yield"))
    )


def match_rate(tenors: list[float], yields: list[float], t: float) -> float | None:
    """Linear interpolation in maturity; flat extrapolation past the short end."""
    if not tenors:
        return None
    if t <= tenors[0]:
        return yields[0]
    for i in range(1, len(tenors)):
        if t <= tenors[i]:
            span = tenors[i] - tenors[i - 1]
            w = (t - tenors[i - 1]) / span
            return yields[i - 1] * (1 - w) + yields[i] * w
    return yields[-1]


def main() -> None:
    files = [
        f for f in sorted(Path("data/index_options_2025").glob("*.parquet"))
        if f.stem in ("SPX", "SPXW")
    ]
    chains_df = pl.concat([prepare(f, "SPX") for f in files], how="vertical_relaxed")
    spot_df = (
        pl.read_parquet("data/indices_2025.parquet")
        .filter(pl.col("symbol") == "SPX")
        .select("date", pl.lit("SPX").alias("symbol"), pl.col("close").alias("spot"))
    )
    boxes_df = box_rates(build_synthetics(chains_df), spot_df)

    curve_df = interpolate_curve(pl.read_parquet("data/yields_2025.parquet"))
    joined = boxes_df.join(curve_df, on="date", how="inner")

    matched = joined.with_columns(
        pl.struct("tenor_y", "yield", "T")
        .map_elements(
            lambda r: match_rate(r["tenor_y"], r["yield"], r["T"]),
            return_dtype=pl.Float64,
        )
        .alias("treasury")
    ).with_columns(((pl.col("implied_rate") - pl.col("treasury")) * 1e4).alias("spread_bp"))

    sofr_df = pl.read_parquet("data/rates_2025.parquet").select(
        "date", pl.col("rate").alias("sofr")
    )
    matched = matched.join(sofr_df, on="date", how="inner").with_columns(
        ((pl.col("implied_rate") - pl.col("sofr")) * 1e4).alias("sofr_spread_bp")
    )

    print("=" * 78)
    print("BOX-IMPLIED RATE vs TERM-MATCHED TREASURY vs OVERNIGHT SOFR")
    print("=" * 78)
    summary = (
        matched.with_columns(
            pl.when(pl.col("T") <= 0.5).then(pl.lit("90-180d"))
            .when(pl.col("T") <= 1.0).then(pl.lit("180-365d"))
            .otherwise(pl.lit(">1y")).alias("bucket")
        )
        .group_by("bucket")
        .agg(
            pl.len().alias("n"),
            (pl.col("implied_rate").median() * 100).round(3).alias("box_%"),
            (pl.col("treasury").median() * 100).round(3).alias("treasury_%"),
            (pl.col("sofr").median() * 100).round(3).alias("sofr_%"),
            pl.col("spread_bp").median().round(1).alias("vs_treas_bp"),
            pl.col("sofr_spread_bp").median().round(1).alias("vs_sofr_bp"),
        )
        .sort("bucket")
    )
    print(summary)

    print(f"\noverall median vs term-matched treasury: {matched['spread_bp'].median():+.1f} bp")
    print(f"overall median vs overnight SOFR       : {matched['sofr_spread_bp'].median():+.1f} bp")
    print(f"stdev vs treasury: {matched['spread_bp'].std():.1f} bp"
          f"   |  stdev vs SOFR: {matched['sofr_spread_bp'].std():.1f} bp")

    daily = (
        matched.group_by("date")
        .agg(
            pl.col("implied_rate").median().alias("box"),
            pl.col("treasury").median().alias("treasury"),
            pl.col("sofr").first().alias("sofr"),
        )
        .sort("date")
    )
    print(f"\ncorrelation box vs treasury: {daily.select(pl.corr('box','treasury')).item():.3f}")
    print(f"correlation box vs SOFR    : {daily.select(pl.corr('box','sofr')).item():.3f}")
    daily.write_parquet("research/implied_rate_termmatched.parquet")
    print("\nwrote research/implied_rate_termmatched.parquet")


if __name__ == "__main__":
    main()
