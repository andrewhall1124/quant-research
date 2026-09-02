"""Score -> quantiles -> position sizes.

Two decisions live here.

**Quantiles are formed daily on the eligible universe.** A name that failed a
filter is not ranked, so the deciles are deciles of what could actually be
traded that day, not of the whole index with holes in it. Days where either
side is too thin to be a portfolio are dropped whole, rather than run as a
two-name decile.

**Sizing is a vega budget in dollars.** Each side carries
`gross_vega_per_side` dollars of vega per vol point, split equally across the
names in the decile — "equal weight by vega". Because the two sides hold the
same number of names at the same per-name vega, the book is vega-neutral by
construction, and every P&L number the engine reports is in dollars.

The quantity that achieves it is `target_vega / position_vega`, where
`position_vega` is the structure's vega per unit. Options are quoted per share
and trade in 100-lots, but nothing here rounds to whole contracts: the study
is about whether the signal pays, and integer lot sizes would add a
name-dependent rounding error that has nothing to do with the question.
"""

import polars as pl


def assign_quantiles(positions_df: pl.DataFrame, n_quantiles: int) -> pl.DataFrame:
    """Rank each day's eligible names into `n_quantiles` equal-count buckets.

    Ranks rather than score cuts, so the buckets stay balanced when the score
    distribution is skewed — which VRP is, being positive in most names.
    """
    return (
        positions_df.with_columns(
            pl.col("score").rank("ordinal").over("date").alias("score_rank"),
            pl.len().over("date").alias("n_names"),
        )
        .with_columns(
            (
                ((pl.col("score_rank") - 1) * n_quantiles / pl.col("n_names"))
                .floor()
                .clip(0, n_quantiles - 1)
                .cast(pl.Int32)
            ).alias("quantile")
        )
    )


def size_positions(
    ranked_df: pl.DataFrame,
    long_quantile: int,
    short_quantile: int,
    gross_vega_per_side: float,
    min_names_per_side: int,
) -> pl.DataFrame:
    """Keep the two extreme deciles and give each name a signed dollar-vega target.

    `side` is +1 long, -1 short. `quantity` is in structure units and is
    signed, so the P&L layer can multiply blindly.
    """
    sides = ranked_df.filter(pl.col("quantile").is_in([long_quantile, short_quantile]))
    sides = sides.with_columns(
        pl.when(pl.col("quantile") == long_quantile).then(1.0).otherwise(-1.0).alias("side")
    )

    # Drop any day where either leg is too thin to be a portfolio.
    counts = sides.group_by("date", "side").agg(pl.len().alias("n_side"))
    usable = (
        counts.filter(pl.col("n_side") >= min_names_per_side)
        .group_by("date")
        .agg(pl.len().alias("n_usable_sides"))
        .filter(pl.col("n_usable_sides") == 2)
        .select("date")
    )
    sides = sides.join(usable, on="date", how="semi").join(counts, on=["date", "side"], how="left")

    return sides.with_columns(
        (pl.col("side") * gross_vega_per_side / pl.col("n_side")).alias("target_vega")
    ).with_columns(
        (pl.col("target_vega") / pl.col("vega")).alias("quantity")
    ).filter(
        pl.col("quantity").is_finite()
    )
