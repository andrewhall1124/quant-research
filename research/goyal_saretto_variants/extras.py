"""Extra per-symbol series the variant signals need, on top of the baseline
panel that `research.goyal_saretto.panel` builds.

Three things the baseline never had to look at:

* A daily ATM implied-vol series, so IV can be compared with its own history
  rather than only with realized vol.
* Realized vol with the earnings-day returns removed, so a name reporting in
  the trailing year is not marked as "high HV" by one jump.
* Midpoint marks on the selected contract 3, 5, 10 and 15 sessions after
  entry, so an early exit can be priced at several horizons.

Run from the project root, after the baseline panel:

    uv run python -m research.goyal_saretto_variants.extras

Writes `results/extras.parquet`, one row per baseline stock-month.
"""

import time
from pathlib import Path

import polars as pl

import data_access_layer as dal
from research.goyal_saretto.panel import HV_MIN_OBSERVATIONS, HV_WINDOW, MAX_IV_ERROR, PANEL_PATH, build_calendar

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"
EXTRAS_PATH = RESULTS_DIR / "extras.parquet"

IV_HISTORY_WINDOW = 252
EXIT_HORIZONS = (3, 5, 10, 15)
SHORT_HV_WINDOWS = (21, 63)


def compute_atm_iv_history(chain: pl.LazyFrame) -> pl.DataFrame:
    """Mean IV of the two closest-to-ATM quoted contracts 20-40 days out, daily."""
    return (
        chain.select("date", "expiration", "strike", "implied_vol", "iv_error", "bid", "underlying_price")
        .with_columns(
            (pl.col("expiration") - pl.col("date")).dt.total_days().alias("dte"),
            (pl.col("strike") / pl.col("underlying_price") - 1).abs().alias("distance"),
        )
        .filter(
            pl.col("dte").is_between(20, 40), pl.col("distance") <= 0.05,
            pl.col("iv_error").abs() <= MAX_IV_ERROR, pl.col("implied_vol") > 0, pl.col("bid") > 0,
        )
        .sort("date", "distance", "dte")
        .group_by("date", maintain_order=True)
        .agg(pl.col("implied_vol").head(2).mean().alias("atm_iv"))
        .sort("date")
        .with_columns(
            pl.col("atm_iv").rolling_mean(IV_HISTORY_WINDOW, min_samples=HV_MIN_OBSERVATIONS).alias("iv_mean_12m"),
            pl.col("atm_iv").rolling_std(IV_HISTORY_WINDOW, min_samples=HV_MIN_OBSERVATIONS).alias("iv_std_12m"),
        )
        .collect()
    )


def compute_realized_vols(chain: pl.LazyFrame, symbol: str, earnings_df: pl.DataFrame) -> pl.DataFrame:
    """Trailing realized vols: short windows, and the 12-month window with
    the earnings day and the session after it removed."""
    spot = (
        chain.filter(pl.col("underlying_price") > 0)
        .group_by("date")
        .agg(pl.col("symbol").last(), pl.col("underlying_price").last().alias("spot"))
    )
    spot = dal.with_corporate_actions(spot).sort("date").with_columns(
        (pl.col("spot") * pl.col("split_ratio") / pl.col("spot").shift(1)).log().alias("log_return")
    ).collect()
    dates = earnings_df.filter(pl.col("symbol") == symbol)["date"]
    is_event = pl.col("date").is_in(dates.implode()) | pl.col("date").shift(1).is_in(dates.implode())
    clean_return = pl.when(is_event).then(None).otherwise(pl.col("log_return"))
    return spot.with_columns(
        *[
            (pl.col("log_return").rolling_std(window, min_samples=window - 2) * (252 ** 0.5)).alias(f"hv_{window}")
            for window in SHORT_HV_WINDOWS
        ],
        (clean_return.rolling_std(HV_WINDOW, min_samples=HV_MIN_OBSERVATIONS) * (252 ** 0.5)).alias("hv_ex_earnings"),
    ).select("date", *[f"hv_{window}" for window in SHORT_HV_WINDOWS], "hv_ex_earnings")


def extract_early_marks(chain: pl.LazyFrame, contracts_df: pl.DataFrame, horizon: int) -> pl.DataFrame:
    """Midpoint of call and put on the exit day for each selected contract,
    columns suffixed with the horizon in sessions.

    A leg with a zero bid is marked at half the ask, not dropped: dropping it
    keeps only straddles still pinned near the strike, which biases a long
    position's mark down. Only a leg with no ask at all is null."""
    exit_col = f"exit_day_{horizon}"
    empty = pl.DataFrame(schema={exit_col: pl.Date, "expiration": pl.Date, "strike": pl.Float64,
                                 f"call_mid_{horizon}": pl.Float64, f"put_mid_{horizon}": pl.Float64,
                                 f"spot_{horizon}": pl.Float64})
    marks = (
        chain.select("date", "expiration", "strike", "right", "bid", "ask", "underlying_price")
        .filter(
            pl.col("date").is_in(contracts_df[exit_col].implode()),
            pl.col("expiration").is_in(contracts_df["expiration"].implode()),
        )
        .collect()
        .join(contracts_df, left_on=["date", "expiration", "strike"], right_on=[exit_col, "expiration", "strike"], how="semi")
    )
    if marks.is_empty():
        return empty
    wide = (
        marks.with_columns(((pl.col("bid") + pl.col("ask")) / 2).alias("mid"), (pl.col("ask") > 0).alias("quoted"))
        .pivot(on="right", index=["date", "expiration", "strike", "underlying_price"], values=["mid", "quoted"])
    )
    if "mid_CALL" not in wide.columns or "mid_PUT" not in wide.columns:
        return empty
    return wide.select(
        pl.col("date").alias(exit_col), "expiration", "strike",
        pl.when(pl.col("quoted_CALL")).then(pl.col("mid_CALL")).alias(f"call_mid_{horizon}"),
        pl.when(pl.col("quoted_PUT")).then(pl.col("mid_PUT")).alias(f"put_mid_{horizon}"),
        pl.col("underlying_price").alias(f"spot_{horizon}"),
    )


def build_extras(panel_df: pl.DataFrame) -> pl.DataFrame:
    sessions = build_calendar()
    session_index = {day: position for position, day in enumerate(sessions)}
    exit_columns = []
    for horizon in EXIT_HORIZONS:
        column = f"exit_day_{horizon}"
        exit_columns.append(column)
        panel_df = panel_df.with_columns(pl.Series(column, [
            sessions[min(session_index[day] + horizon, len(sessions) - 1)] for day in panel_df["entry_day"]
        ]))
    earnings_df = dal.load_earnings().select("symbol", "date")

    started = time.time()
    pieces = []
    symbols = panel_df["symbol"].unique().sort().to_list()
    for count, symbol in enumerate(symbols, 1):
        rows_df = panel_df.filter(pl.col("symbol") == symbol)
        chain = dal.load_option_greeks(symbol, lazy=True)
        piece = (
            rows_df.select("symbol", "month", "formation_day", *exit_columns, "expiration", "strike")
            .join(compute_atm_iv_history(chain), left_on="formation_day", right_on="date", how="left")
            .join(compute_realized_vols(chain, symbol, earnings_df), left_on="formation_day", right_on="date", how="left")
        )
        for horizon in EXIT_HORIZONS:
            marks_df = extract_early_marks(chain, rows_df.select(f"exit_day_{horizon}", "expiration", "strike"), horizon)
            piece = piece.join(marks_df, on=[f"exit_day_{horizon}", "expiration", "strike"], how="left")
        pieces.append(piece)
        if count % 100 == 0 or count == len(symbols):
            print(f"  {count}/{len(symbols)} symbols, {time.time() - started:.0f}s", flush=True)
    return pl.concat(pieces)


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    panel_df = pl.read_parquet(PANEL_PATH)
    extras_df = build_extras(panel_df)
    extras_df.write_parquet(EXTRAS_PATH)
    print(f"wrote {EXTRAS_PATH}: {extras_df.height} rows; "
          f"iv history on {extras_df['iv_mean_12m'].is_not_null().mean():.0%}, "
          f"10-session marks on {extras_df['call_mid_10'].is_not_null().mean():.0%}")


if __name__ == "__main__":
    main()
