"""Quoted spread per dollar of vega — the quantity that decides tradeability.

`REPORT.md` shows the strategy has a real gross signal and cannot pay its own
bid-ask spread. Break-even is set by how much spread it costs to buy a dollar
of vega:

    cost per $vega = (ask - bid) * 100 / vega

`vega` as stored is already dollars per contract per vol point, so this is the
dollars of quoted edge crossed to acquire one dollar of vega exposure. It is
the denominator of the whole problem, and this module measures how it varies
across choices the main study fixed arbitrarily.

The finding: ATM vega grows as sqrt(T) while quoted spreads are closer to
tick-driven, so tenor moves this by ~46% and open interest by another ~2.5x,
while underlying price does not move it at all.

    uv run python -m research.single_name_iv_reversion.cost_efficiency
"""

import polars as pl

from data_access_layer import paths

# Every 7th symbol. The medians are stable well before the full universe, and
# this keeps the scan to ~1M contract-days rather than 20M.
SAMPLE_STRIDE = 7
SAMPLE_SIZE = 75
MAX_MONEYNESS = 0.03


def load_atm(symbol: str, with_open_interest: bool) -> pl.DataFrame | None:
    """Near-the-money quoted contracts for one name, optionally joined to OI."""
    greeks = (
        pl.scan_parquet(paths.OPTION_GREEKS_DIR / f"{symbol}.parquet")
        .with_columns(
            pl.col("underlying_timestamp").dt.date().alias("date"),
            pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d"),
        )
        .with_columns(
            (pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"),
            (pl.col("strike") / pl.col("underlying_price") - 1).alias("moneyness"),
        )
        .filter(
            pl.col("moneyness").abs() <= MAX_MONEYNESS,
            pl.col("bid") > 0,
            pl.col("ask") >= pl.col("bid"),
            pl.col("vega") > 1,
            pl.col("iv_error").abs() <= 1.0,
        )
        .select(
            "date", "symbol", "expiration", "strike", "right",
            "dte", "bid", "ask", "vega", "underlying_price",
        )
    )
    if not with_open_interest:
        return greeks.collect()

    path = paths.OPEN_INTEREST_DIR / f"{symbol}.parquet"
    if not path.exists():
        return None
    open_interest = (
        pl.scan_parquet(path)
        .with_columns(
            pl.col("timestamp").dt.date().alias("date"),
            pl.col("expiration").str.strptime(pl.Date, "%Y-%m-%d"),
        )
        .select("date", "symbol", "expiration", "strike", "right", "open_interest")
    )
    return greeks.join(
        open_interest,
        on=["date", "symbol", "expiration", "strike", "right"],
        how="inner",
    ).collect()


def add_cost_metrics(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        ((pl.col("ask") - pl.col("bid")) * 100 / pl.col("vega")).alias("cost_per_vega"),
        (
            (pl.col("ask") - pl.col("bid"))
            / ((pl.col("ask") + pl.col("bid")) / 2)
        ).alias("rel_spread"),
    )


def summarize(frame: pl.DataFrame, bucket: pl.Expr) -> pl.DataFrame:
    return (
        frame.with_columns(bucket.alias("bucket"))
        .group_by("bucket")
        .agg(
            pl.len().alias("n"),
            pl.col("vega").median().alias("median_vega"),
            pl.col("cost_per_vega").median().alias("cost_per_vega"),
            pl.col("rel_spread").median().alias("rel_spread"),
        )
        .sort("bucket")
    )


def tenor_bucket() -> pl.Expr:
    return (
        pl.when(pl.col("dte") < 45).then(pl.lit("1. 15-45d"))
        .when(pl.col("dte") < 75).then(pl.lit("2. 45-75d"))
        .when(pl.col("dte") < 120).then(pl.lit("3. 75-120d"))
        .when(pl.col("dte") < 180).then(pl.lit("4. 120-180d"))
        .otherwise(pl.lit("5. 180-250d"))
    )


def price_bucket() -> pl.Expr:
    return (
        pl.when(pl.col("underlying_price") < 50).then(pl.lit("1. <$50"))
        .when(pl.col("underlying_price") < 150).then(pl.lit("2. $50-150"))
        .when(pl.col("underlying_price") < 400).then(pl.lit("3. $150-400"))
        .otherwise(pl.lit("4. >$400"))
    )


def open_interest_bucket() -> pl.Expr:
    return (
        pl.when(pl.col("open_interest") < 10).then(pl.lit("1. <10"))
        .when(pl.col("open_interest") < 50).then(pl.lit("2. 10-50"))
        .when(pl.col("open_interest") < 250).then(pl.lit("3. 50-250"))
        .when(pl.col("open_interest") < 1000).then(pl.lit("4. 250-1k"))
        .otherwise(pl.lit("5. >1k"))
    )


def main() -> None:
    symbols = paths.available_option_symbols()[::SAMPLE_STRIDE][:SAMPLE_SIZE]

    plain = add_cost_metrics(
        pl.concat([load_atm(s, False) for s in symbols], how="vertical_relaxed")
    )
    print(f"ATM contract-days: {plain.height:,} across {plain['symbol'].n_unique()} names\n")
    print("=== by tenor ===")
    print(summarize(plain, tenor_bucket()))
    print("\n=== by underlying price (30-day contracts only) ===")
    print(summarize(plain.filter(pl.col("dte").is_between(23, 37)), price_bucket()))

    joined = [load_atm(s, True) for s in symbols]
    with_oi = add_cost_metrics(
        pl.concat([f for f in joined if f is not None], how="vertical_relaxed")
    )
    for label, window in (("30-day (23-37 dte)", (23, 37)), ("90-150 dte", (90, 150))):
        print(f"\n=== by open interest, {label} ===")
        print(summarize(
            with_oi.filter(pl.col("dte").is_between(*window)), open_interest_bucket()
        ))


if __name__ == "__main__":
    main()
