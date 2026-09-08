"""Idiosyncratic momentum: the parent study's machinery on residual reference returns.

Two signal sources from `panel.py` — the residual itself and the residual
scaled by its trailing volatility — each at the momentum windows (3, 6, 12
months skipping the last month; 1 month and 12 months unskipped as
controls), scored on deciles, a rank book and the MVO book, all trading the
actual reference unit. Run from the project root, after `panel.py`:

    uv run python -m research.idio_momentum.analysis
"""

from __future__ import annotations

import logging

import polars as pl

from research.idio_momentum.panel import RESULTS_DIR, STUDY_DIR
from research.vol_momentum.analysis import MOMENTUM_WINDOWS, SUMMARY_COLUMNS, Context, run_study
from research.vol_momentum.panel import RESULTS_DIR as MOMENTUM_RESULTS


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    pl.Config.set_tbl_cols(30)
    pl.Config.set_tbl_width_chars(260)
    pl.Config.set_float_precision(3)
    ctx = Context(MOMENTUM_RESULTS)
    summaries = []
    for version in ("residual", "scaled"):
        source_df = pl.read_parquet(RESULTS_DIR / f"{version}_reference.parquet")
        version_dir = RESULTS_DIR / version
        version_dir.mkdir(exist_ok=True)
        summary_df = run_study(
            MOMENTUM_WINDOWS, sign=-1.0, ctx=ctx, results_dir=version_dir, figures_dir=STUDY_DIR / "figures",
            figure_name=f"idio_momentum_{version}.png", title=f"idiosyncratic momentum ({version} reference return)", signal_reference_df=source_df,
        )
        summaries.append(summary_df.with_columns(pl.lit(version).alias("version")))
        print(f"\n=== {version} ===")
        print(summary_df.select(SUMMARY_COLUMNS))
    pl.concat(summaries).write_csv(RESULTS_DIR / "summary.csv")


if __name__ == "__main__":
    main()
