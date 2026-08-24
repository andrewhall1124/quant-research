import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def imports():
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import polars as pl

    return Path, alt, mo, pl


@app.cell
def title(mo):
    mo.md(
        r"""
        # SPX Options Factsheet

        What the 2025 SPX chain data contains, how many box spreads it yields, and
        what rate those boxes imply.

        Source: ThetaData EOD option chains (free tier), SPX and SPXW. Companion to
        `research/README.md`, which carries the interpretation.
        """
    )
    return


@app.cell
def load_chains(Path, pl):
    index_files = [
        f
        for f in sorted(Path("data/index_options_2025").glob("*.parquet"))
        if f.stem in ("SPX", "SPXW")
    ]

    chains_lf = pl.scan_parquet(index_files).with_columns(
        pl.col("created").dt.date().alias("date"),
        pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d").alias("exp"),
    )
    return chains_lf, index_files


@app.cell
def chain_header(mo):
    mo.md(r"""## 1 · Chain coverage""")
    return


@app.cell
def coverage(chains_lf, index_files, mo, pl):
    coverage_df = chains_lf.select(
        pl.len().alias("rows"),
        pl.col("date").n_unique().alias("trading_days"),
        pl.col("exp").n_unique().alias("expirations"),
        pl.col("strike").n_unique().alias("strikes"),
        pl.col("date").min().alias("first_date"),
        pl.col("date").max().alias("last_date"),
    ).collect()

    disk_mb = sum(f.stat().st_size for f in index_files) / 1e6

    coverage_summary = mo.md(
        f"""
        | Metric | Value |
        |---|---|
        | Symbols | {", ".join(f.stem for f in index_files)} |
        | Rows | {coverage_df["rows"][0]:,} |
        | Trading days | {coverage_df["trading_days"][0]} |
        | Distinct expirations | {coverage_df["expirations"][0]:,} |
        | Distinct strikes | {coverage_df["strikes"][0]:,} |
        | Date range | {coverage_df["first_date"][0]} → {coverage_df["last_date"][0]} |
        | On disk | {disk_mb:,.0f} MB |
        """
    )
    coverage_summary
    return (coverage_df,)


@app.cell
def quote_quality_header(mo):
    mo.md(
        r"""
        ### Quote quality

        A row is only usable for a box if it is genuinely two-sided. Deep out-of-the-money
        contracts routinely quote a zero bid, which is why the usable count is well below
        the raw row count.
        """
    )
    return


@app.cell
def quote_quality(chains_lf, mo, pl):
    quality_df = chains_lf.select(
        pl.len().alias("all_rows"),
        (pl.col("bid") > 0).sum().alias("nonzero_bid"),
        ((pl.col("bid") > 0) & (pl.col("ask") > pl.col("bid"))).sum().alias("two_sided"),
        (pl.col("volume") > 0).sum().alias("traded"),
    ).collect()

    all_rows = quality_df["all_rows"][0]
    quality_summary = mo.md(
        f"""
        | Filter | Rows | Share |
        |---|---|---|
        | All quoted rows | {all_rows:,} | 100.0% |
        | Bid > 0 | {quality_df["nonzero_bid"][0]:,} | {quality_df["nonzero_bid"][0] / all_rows:.1%} |
        | Two-sided (ask > bid > 0) | {quality_df["two_sided"][0]:,} | {quality_df["two_sided"][0] / all_rows:.1%} |
        | Traded that day (volume > 0) | {quality_df["traded"][0]:,} | {quality_df["traded"][0] / all_rows:.1%} |
        """
    )
    quality_summary
    return (quality_df,)


@app.cell
def maturity_header(mo):
    mo.md(r"""### Where the contracts sit""")
    return


@app.cell
def maturity_mix(alt, chains_lf, pl):
    maturity_df = (
        chains_lf.filter((pl.col("bid") > 0) & (pl.col("ask") > pl.col("bid")))
        .with_columns((pl.col("exp") - pl.col("date")).dt.total_days().alias("dte"))
        .filter(pl.col("dte").is_between(0, 900))
        .with_columns(
            pl.when(pl.col("dte") <= 7).then(pl.lit("0-7d"))
            .when(pl.col("dte") <= 30).then(pl.lit("8-30d"))
            .when(pl.col("dte") <= 90).then(pl.lit("31-90d"))
            .when(pl.col("dte") <= 180).then(pl.lit("91-180d"))
            .when(pl.col("dte") <= 365).then(pl.lit("181-365d"))
            .otherwise(pl.lit(">1y"))
            .alias("bucket")
        )
        .group_by("bucket")
        .agg(pl.len().alias("contracts"))
        .collect()
    )

    bucket_order = ["0-7d", "8-30d", "31-90d", "91-180d", "181-365d", ">1y"]

    maturity_chart = (
        alt.Chart(maturity_df.to_pandas())
        .mark_bar(cornerRadiusEnd=4, color="#2D6FB8")
        .encode(
            x=alt.X("bucket:N", sort=bucket_order, title="Days to expiration"),
            y=alt.Y("contracts:Q", title="Two-sided contract-days"),
            tooltip=["bucket", alt.Tooltip("contracts:Q", format=",")],
        )
        .properties(height=240, title="Two-sided quotes by maturity")
    )
    maturity_chart
    return maturity_df, bucket_order


@app.cell
def box_header(mo):
    mo.md(
        r"""
        ## 2 · Box construction

        A box pairs two strikes at one expiration into a synthetic zero-coupon bond:

        $$(C_1 - P_1) - (C_2 - P_2) = (K_2 - K_1)\,e^{-rT}$$

        Spot cancels, so no underlying price and no dividend assumption is needed.
        The funnel below is steep — most of the chain cannot produce a usable rate.
        """
    )
    return


@app.cell
def box_funnel(coverage_df, mo, pl, quality_df):
    boxes_df = pl.read_parquet("research/tradability.parquet")
    rate_boxes_df = pl.read_parquet("research/implied_rate_termmatched.parquet")

    funnel_rows = [
        ("Quoted rows", coverage_df["rows"][0]),
        ("Two-sided", quality_df["two_sided"][0]),
        ("Boxes formed (±15% moneyness, >5% wide)", boxes_df.height),
    ]
    funnel_md = "\n".join(f"| {label} | {value:,} |" for label, value in funnel_rows)

    box_funnel_summary = mo.md(
        f"""
        | Stage | Count |
        |---|---|
        {funnel_md}
        | Days with a measurable rate | {rate_boxes_df.height:,} |
        """
    )
    box_funnel_summary
    return boxes_df, rate_boxes_df


@app.cell
def precision_header(mo):
    mo.md(
        r"""
        ### Why short-dated boxes are unusable

        Quote noise enters the implied rate divided by `width × T`. As `T` shrinks the
        error explodes, so a one-week box carries tens of percentage points of rate
        uncertainty regardless of how tight the quotes are. This is a property of the
        instrument, not of the filter.
        """
    )
    return


@app.cell
def precision_table(boxes_df, mo, pl):
    precision_df = (
        boxes_df.with_columns(
            ((pl.col("ca1") - pl.col("cb1")) + (pl.col("pa1") - pl.col("pb1"))
             + (pl.col("ca2") - pl.col("cb2")) + (pl.col("pa2") - pl.col("pb2"))
             ).alias("total_spread")
        )
        .with_columns(
            (pl.col("total_spread") / (pl.col("width") * pl.col("T")) * 1e4).alias("precision_bp")
        )
        .with_columns(
            pl.when(pl.col("T") <= 0.5).then(pl.lit("90-180d"))
            .when(pl.col("T") <= 1.0).then(pl.lit("180-365d"))
            .otherwise(pl.lit(">1y"))
            .alias("bucket")
        )
        .group_by("bucket")
        .agg(
            pl.len().alias("boxes"),
            pl.col("width").median().round(0).alias("median_width"),
            pl.col("precision_bp").median().round(0).alias("median_precision_bp"),
            (pl.col("r_mid").median() * 100).round(3).alias("median_rate_pct"),
        )
        .sort("bucket")
    )
    mo.ui.table(precision_df.to_pandas(), selection=None)
    return (precision_df,)


@app.cell
def series_header(mo):
    mo.md(
        r"""
        ## 3 · The box rate through 2025

        Daily medians. `Treasury` is the CBOE yield curve interpolated to each box's own
        maturity; `SOFR` is the overnight rate, shown for contrast rather than as a
        like-for-like benchmark — the measurable boxes run 6–12 months.
        """
    )
    return


@app.cell
def rate_chart(alt, pl, rate_boxes_df):
    long_df = (
        rate_boxes_df.select(
            "date",
            (pl.col("box") * 100).alias("Box-implied"),
            (pl.col("treasury") * 100).alias("Treasury (term-matched)"),
            (pl.col("sofr") * 100).alias("SOFR (overnight)"),
        )
        .unpivot(index="date", variable_name="series", value_name="rate")
        .sort("date")
    )

    hover = alt.selection_point(
        fields=["date"], nearest=True, on="mouseover", empty=False, clear="mouseout"
    )

    base = alt.Chart(long_df.to_pandas()).encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("rate:Q", title="Rate (%)", scale=alt.Scale(zero=False)),
        color=alt.Color(
            "series:N",
            title=None,
            scale=alt.Scale(
                domain=["Box-implied", "Treasury (term-matched)", "SOFR (overnight)"],
                range=["#C2670E", "#2D6FB8", "#B0407A"],
            ),
            legend=alt.Legend(orient="top"),
        ),
    )

    rate_series_chart = (
        base.mark_line(strokeWidth=2)
        + base.mark_point(size=60, filled=True).encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0))
        ).add_params(hover)
        + base.mark_rule(color="#999").encode(
            tooltip=[
                alt.Tooltip("date:T", title="Date"),
                alt.Tooltip("series:N", title="Series"),
                alt.Tooltip("rate:Q", title="Rate", format=".3f"),
            ]
        ).transform_filter(hover)
    ).properties(height=340, title="Box-implied rate vs benchmarks, 2025")

    rate_series_chart
    return long_df, rate_series_chart


@app.cell
def stats_header(mo):
    mo.md(r"""### Summary statistics""")
    return


@app.cell
def summary_stats(mo, pl, rate_boxes_df):
    stats_df = rate_boxes_df.select(
        (pl.col("box").mean() * 100).round(3).alias("box_mean_pct"),
        (pl.col("treasury").mean() * 100).round(3).alias("treasury_mean_pct"),
        (pl.col("sofr").mean() * 100).round(3).alias("sofr_mean_pct"),
        ((pl.col("box") - pl.col("treasury")).median() * 1e4).round(1).alias("vs_treasury_bp"),
        ((pl.col("box") - pl.col("sofr")).median() * 1e4).round(1).alias("vs_sofr_bp"),
    )
    correlations = rate_boxes_df.select(
        pl.corr("box", "treasury").round(3).alias("corr_treasury"),
        pl.corr("box", "sofr").round(3).alias("corr_sofr"),
    )

    stats_summary = mo.md(
        f"""
        | Statistic | Value |
        |---|---|
        | Mean box rate | {stats_df["box_mean_pct"][0]}% |
        | Mean term-matched Treasury | {stats_df["treasury_mean_pct"][0]}% |
        | Mean overnight SOFR | {stats_df["sofr_mean_pct"][0]}% |
        | Median spread vs Treasury | {stats_df["vs_treasury_bp"][0]:+} bp |
        | Median spread vs SOFR | {stats_df["vs_sofr_bp"][0]:+} bp |
        | Correlation with Treasury | {correlations["corr_treasury"][0]} |
        | Correlation with SOFR | {correlations["corr_sofr"][0]} |

        The box tracks the term-matched Treasury curve far better than it tracks
        overnight SOFR, and sits above it — a box is unsecured synthetic financing,
        while Treasuries carry a collateral premium.
        """
    )
    stats_summary
    return correlations, stats_df


@app.cell
def footer(mo):
    mo.md(
        r"""
        ---

        Regenerate the inputs with `research/pull_index_options.py`, `research/term_matched.py`
        and `research/tradability.py`. Run this notebook with `marimo edit research/spx_factsheet.py`.
        """
    )
    return


if __name__ == "__main__":
    app.run()
