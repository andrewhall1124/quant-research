"""Can the box-vs-Treasury spread actually be captured?

The +16bp headline is computed at bid/ask midpoints, which nobody trades at. A
box has four legs, and lending through it means paying the offer on both longs
and hitting the bid on both shorts. This prices the same boxes at executable
levels and asks whether any edge survives.

It also rebuilds the Treasury benchmark from FRED constant maturities (1m, 3m,
6m, 1y, 2y) rather than the CBOE indices, which had only a 13-week point before
jumping to five years -- straddling the 6-12 month region where the boxes live.
"""

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from implied_rates import DAYS_PER_YEAR
from run_analysis import prepare


def build_legs(chains_df: pl.DataFrame) -> pl.DataFrame:
    """One row per (date, exp, strike) carrying both legs' full bid/ask."""
    calls = chains_df.filter(pl.col("right") == "CALL").select(
        "symbol", "date", "exp", "strike", "T",
        pl.col("bid").alias("cb"), pl.col("ask").alias("ca"),
    )
    puts = chains_df.filter(pl.col("right") == "PUT").select(
        "symbol", "date", "exp", "strike",
        pl.col("bid").alias("pb"), pl.col("ask").alias("pa"),
    )
    return calls.join(puts, on=["symbol", "date", "exp", "strike"], how="inner")


def build_boxes(legs_df: pl.DataFrame, spot_df: pl.DataFrame) -> pl.DataFrame:
    banded = legs_df.join(spot_df, on=["symbol", "date"], how="inner").filter(
        (pl.col("strike") / pl.col("spot") - 1).abs() <= 0.15
    )
    ranked = banded.with_columns(
        pl.col("strike").rank("ordinal").over("symbol", "date", "exp").alias("lo_rank"),
        pl.col("strike").rank("ordinal", descending=True).over("symbol", "date", "exp").alias("hi_rank"),
        pl.len().over("symbol", "date", "exp").alias("n_strikes"),
    ).filter(pl.col("n_strikes") >= 6)

    lo = ranked.filter(pl.col("lo_rank") <= 5).select(
        "symbol", "date", "exp", "T", "spot", pl.col("lo_rank").alias("pair"),
        pl.col("strike").alias("k1"),
        pl.col("cb").alias("cb1"), pl.col("ca").alias("ca1"),
        pl.col("pb").alias("pb1"), pl.col("pa").alias("pa1"),
    )
    hi = ranked.filter(pl.col("hi_rank") <= 5).select(
        "symbol", "date", "exp", pl.col("hi_rank").alias("pair"),
        pl.col("strike").alias("k2"),
        pl.col("cb").alias("cb2"), pl.col("ca").alias("ca2"),
        pl.col("pb").alias("pb2"), pl.col("pa").alias("pa2"),
    )

    return (
        lo.join(hi, on=["symbol", "date", "exp", "pair"], how="inner")
        .filter(pl.col("k2") > pl.col("k1") * 1.05)
        .with_columns(
            (pl.col("k2") - pl.col("k1")).alias("width"),
            # Mid: the headline number.
            (((pl.col("ca1") + pl.col("cb1")) / 2 - (pl.col("pa1") + pl.col("pb1")) / 2)
             - ((pl.col("ca2") + pl.col("cb2")) / 2 - (pl.col("pa2") + pl.col("pb2")) / 2)
             ).alias("cost_mid"),
            # Lending (buy the box): pay the offer on the long call K1 and long put
            # K2, hit the bid on the short put K1 and short call K2.
            ((pl.col("ca1") - pl.col("pb1")) - (pl.col("cb2") - pl.col("pa2"))).alias("cost_buy"),
            # Borrowing (sell the box): the mirror -- you receive less.
            ((pl.col("cb1") - pl.col("pa1")) - (pl.col("ca2") - pl.col("pb2"))).alias("cost_sell"),
        )
        .filter((pl.col("cost_mid") > 0) & (pl.col("cost_buy") > 0) & (pl.col("cost_sell") > 0))
        .filter(pl.col("T") > 90 / DAYS_PER_YEAR)
        .with_columns(
            ((pl.col("width") / pl.col("cost_mid")).log() / pl.col("T")).alias("r_mid"),
            ((pl.col("width") / pl.col("cost_buy")).log() / pl.col("T")).alias("r_lend"),
            ((pl.col("width") / pl.col("cost_sell")).log() / pl.col("T")).alias("r_borrow"),
        )
        .filter(pl.col("r_mid").is_between(-0.02, 0.15))
    )


TENORS = {"DGS1MO": 1/12, "DGS3MO": 0.25, "DGS6MO": 0.5, "DGS1": 1.0, "DGS2": 2.0}


def treasury_curve() -> pl.DataFrame:
    fred_df = pl.read_parquet("data/fred_rates.parquet").filter(pl.col("family") == "treasury")
    return (
        fred_df.sort("date", "tenor_y")
        .group_by("date")
        .agg(pl.col("tenor_y"), pl.col("rate"))
    )


def match(tenors, rates, t):
    if not tenors:
        return None
    if t <= tenors[0]:
        return rates[0]
    for i in range(1, len(tenors)):
        if t <= tenors[i]:
            w = (t - tenors[i-1]) / (tenors[i] - tenors[i-1])
            return rates[i-1] * (1 - w) + rates[i] * w
    return rates[-1]


def main() -> None:
    files = [f for f in sorted(Path("data/index_options_2025").glob("*.parquet"))
             if f.stem in ("SPX", "SPXW")]
    chains_df = pl.concat([prepare(f, "SPX") for f in files], how="vertical_relaxed")
    spot_df = (
        pl.read_parquet("data/indices_2025.parquet")
        .filter(pl.col("symbol") == "SPX")
        .select("date", pl.lit("SPX").alias("symbol"), pl.col("close").alias("spot"))
    )
    boxes = build_boxes(build_legs(chains_df), spot_df)
    boxes = boxes.join(treasury_curve(), on="date", how="inner").with_columns(
        pl.struct("tenor_y", "rate", "T")
        .map_elements(lambda r: match(r["tenor_y"], r["rate"], r["T"]), return_dtype=pl.Float64)
        .alias("tsy")
    )

    print("=" * 76)
    print("EXECUTION COST: what crossing the spread does to the box rate")
    print("=" * 76)
    print(f"boxes analysed (T > 90d): {boxes.height:,}")
    q = lambda c: boxes[c].median()
    print(f"\n  rate at mid            : {q('r_mid')*100:.3f}%")
    print(f"  rate lending (pay ask) : {q('r_lend')*100:.3f}%")
    print(f"  rate borrowing (hit bid): {q('r_borrow')*100:.3f}%")
    print(f"\n  round-trip bid/ask cost: {(q('r_borrow')-q('r_lend'))*1e4:,.0f} bp")
    print(f"  one-way cost from mid  : {(q('r_mid')-q('r_lend'))*1e4:,.0f} bp")
    print(f"\n  term-matched Treasury  : {q('tsy')*100:.3f}%")

    print("\n" + "=" * 76)
    print("EDGE vs TREASURY, before and after execution cost")
    print("=" * 76)
    edges = boxes.with_columns(
        ((pl.col("r_mid") - pl.col("tsy")) * 1e4).alias("edge_mid_bp"),
        ((pl.col("r_lend") - pl.col("tsy")) * 1e4).alias("edge_lend_bp"),
    )
    print(f"  median edge at mid       : {edges['edge_mid_bp'].median():+.1f} bp")
    print(f"  median edge if executed  : {edges['edge_lend_bp'].median():+.1f} bp")
    win = (edges["edge_lend_bp"] > 0).mean()
    print(f"  boxes profitable at touch: {win*100:.1f}%")

    print("\n" + "=" * 76)
    print("BY MATURITY (execution cost falls as T grows)")
    print("=" * 76)
    bucket = edges.with_columns(
        pl.when(pl.col("T") <= 0.5).then(pl.lit("90-180d"))
        .when(pl.col("T") <= 1.0).then(pl.lit("180-365d"))
        .otherwise(pl.lit(">1y")).alias("bucket")
    ).group_by("bucket").agg(
        pl.len().alias("n"),
        ((pl.col("r_mid") - pl.col("r_lend")) * 1e4).median().round(0).alias("exec_cost_bp"),
        pl.col("edge_mid_bp").median().round(1).alias("edge_mid_bp"),
        pl.col("edge_lend_bp").median().round(1).alias("edge_exec_bp"),
        ((pl.col("edge_lend_bp") > 0).mean() * 100).round(1).alias("pct_profitable"),
    ).sort("bucket")
    print(bucket)

    boxes.write_parquet("research/tradability.parquet")
    print("\nwrote research/tradability.parquet")


if __name__ == "__main__":
    main()
