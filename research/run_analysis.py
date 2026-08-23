"""Compare option-implied risk-free rates to SOFR. See implied_rates.py for method."""

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from implied_rates import DAYS_PER_YEAR, box_rates, build_synthetics, parity_rates


def prepare(path: Path, symbol_override: str | None = None) -> pl.DataFrame:
    df = pl.read_parquet(path).with_columns(
        pl.col("created").dt.date().alias("date"),
        pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d").alias("exp"),
        ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
    )
    if symbol_override:
        df = df.with_columns(pl.lit(symbol_override).alias("symbol"))
    return df.filter((pl.col("bid") > 0) & (pl.col("ask") > pl.col("bid"))).with_columns(
        ((pl.col("exp") - pl.col("date")).dt.total_days() / DAYS_PER_YEAR).alias("T")
    )


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main() -> None:
    sofr_df = pl.read_parquet("data/rates_2025.parquet").select(
        "date", pl.col("rate").alias("sofr")
    )
    spx_spot_df = (
        pl.read_parquet("data/indices_2025.parquet")
        .filter(pl.col("symbol") == "SPX")
        .select("date", pl.col("close").alias("spot"))
    )

    # SPX and SPXW quote the same underlying index; SPXW is just the weekly series.
    index_files = sorted(Path("data/index_options_2025").glob("*.parquet"))
    index_files = [f for f in index_files if f.stem in ("SPX", "SPXW")]
    chains_df = pl.concat([prepare(f, "SPX") for f in index_files], how="vertical_relaxed")

    synth_df = build_synthetics(chains_df)
    spot_keyed = spx_spot_df.with_columns(pl.lit("SPX").alias("symbol"))
    boxes_df = box_rates(synth_df, spot_keyed)

    section("COVERAGE")
    print(f"index chains       : {chains_df.height:,} quoted rows over {chains_df['date'].n_unique()} dates")
    print(f"synthetic forwards : {synth_df.height:,}")
    print(f"boxes after filters: {boxes_df.height:,}")

    section("LEVEL: box-implied rate vs SOFR")
    daily_df = (
        boxes_df.group_by("date")
        .agg(pl.col("implied_rate").median().alias("box"), pl.len().alias("n_boxes"))
        .join(sofr_df, on="date", how="inner")
        .sort("date")
        .with_columns(((pl.col("box") - pl.col("sofr")) * 1e4).alias("spread_bp"))
    )
    print(f"days with both     : {daily_df.height}")
    print(f"mean box rate      : {daily_df['box'].mean() * 100:.3f}%")
    print(f"mean SOFR          : {daily_df['sofr'].mean() * 100:.3f}%")
    print(f"mean spread        : {daily_df['spread_bp'].mean():+.1f} bp")
    print(f"median spread      : {daily_df['spread_bp'].median():+.1f} bp")
    print(f"stdev of spread    : {daily_df['spread_bp'].std():.1f} bp")
    corr = daily_df.select(pl.corr("box", "sofr")).item()
    print(f"correlation        : {corr:.3f}")

    section("TERM STRUCTURE: implied rate by maturity vs spot SOFR")
    term_df = (
        boxes_df.join(sofr_df, on="date", how="inner")
        .with_columns(
            pl.when(pl.col("T") <= 30 / DAYS_PER_YEAR).then(pl.lit("1  <=30d"))
            .when(pl.col("T") <= 90 / DAYS_PER_YEAR).then(pl.lit("2  30-90d"))
            .when(pl.col("T") <= 180 / DAYS_PER_YEAR).then(pl.lit("3  90-180d"))
            .when(pl.col("T") <= 365 / DAYS_PER_YEAR).then(pl.lit("4  180-365d"))
            .otherwise(pl.lit("5  >1y")).alias("bucket")
        )
        .group_by("bucket")
        .agg(
            pl.len().alias("n"),
            (pl.col("implied_rate").median() * 100).round(3).alias("box_pct"),
            (pl.col("sofr").median() * 100).round(3).alias("sofr_pct"),
            ((pl.col("implied_rate") - pl.col("sofr")).median() * 1e4).round(1).alias("spread_bp"),
        )
        .sort("bucket")
    )
    print(term_df)

    section("MONTHLY TRACKING")
    monthly_df = (
        daily_df.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .group_by("month")
        .agg(
            pl.len().alias("days"),
            (pl.col("box").mean() * 100).round(3).alias("box_pct"),
            (pl.col("sofr").mean() * 100).round(3).alias("sofr_pct"),
            pl.col("spread_bp").mean().round(1).alias("spread_bp"),
        )
        .sort("month")
    )
    with pl.Config(tbl_rows=20):
        print(monthly_df)

    section("DIVIDEND CHECK: put-call parity vs box on the same index")
    # Parity ignores dividends, so parity_rate - box_rate should recover roughly
    # the negative of the index dividend yield.
    parity_df = parity_rates(synth_df, spot_keyed)
    parity_daily = parity_df.group_by("date").agg(
        pl.col("implied_rate").median().alias("parity")
    )
    both_df = daily_df.join(parity_daily, on="date", how="inner")
    implied_div = (both_df["box"] - both_df["parity"]).median()
    print(f"median box rate    : {both_df['box'].median() * 100:.3f}%")
    print(f"median parity rate : {both_df['parity'].median() * 100:.3f}%")
    print(f"implied dividend yd: {implied_div * 100:+.3f}%  (S&P 500 ran ~1.2-1.3% in 2025)")

    section("AMERICAN vs EUROPEAN: equity boxes for contrast")
    equity_rows = []
    for name in ["AAPL", "MSFT", "AMZN"]:
        path = Path(f"data/options_2025/{name}.parquet")
        if not path.exists():
            continue
        eq_chain = prepare(path)
        eq_spot = (
            pl.read_parquet("data/underlying_2025.parquet")
            .filter(pl.col("symbol") == name)
            .select(pl.col("created").dt.date().alias("date"),
                    pl.lit(name).alias("symbol"),
                    pl.col("close").alias("spot"))
        )
        eq_boxes = box_rates(build_synthetics(eq_chain), eq_spot)
        if eq_boxes.height:
            equity_rows.append({
                "symbol": name,
                "n_boxes": eq_boxes.height,
                "median_pct": round(eq_boxes["implied_rate"].median() * 100, 3),
            })
    print(pl.DataFrame(equity_rows) if equity_rows else "no equity boxes survived filters")

    daily_df.write_parquet("research/implied_rate_daily.parquet")
    term_df.write_parquet("research/implied_rate_term.parquet")
    print("\nwrote research/implied_rate_daily.parquet, research/implied_rate_term.parquet")


if __name__ == "__main__":
    main()
