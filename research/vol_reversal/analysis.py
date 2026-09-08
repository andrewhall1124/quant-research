"""Vol reversal: sell the names whose reference straddle just paid, buy the ones that just lost.

Same machinery as `research.vol_momentum.analysis`, sign +1 (a high trailing
P&L is scored as *rich*), on windows of 5, 10, 21, 30 and 42 sessions with no
skip. Run from the project root, after `research.vol_momentum.panel`:

    uv run python -m research.vol_reversal.analysis
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from research.vol_momentum.analysis import SUMMARY_COLUMNS, Context, run_study
from research.vol_momentum.panel import RESULTS_DIR as MOMENTUM_RESULTS

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"

# the skip-1 variants leave out the most recent session, which is where bid-ask bounce in the marks lives
REVERSAL_WINDOWS = [("1w", 5, 0), ("1w_skip1", 5, 1), ("2w", 10, 0), ("1m", 21, 0), ("1m_skip1", 21, 1), ("2m", 42, 0)]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    pl.Config.set_tbl_cols(30)
    pl.Config.set_tbl_width_chars(260)
    pl.Config.set_float_precision(3)
    RESULTS_DIR.mkdir(exist_ok=True)
    ctx = Context(MOMENTUM_RESULTS)
    summary_df = run_study(REVERSAL_WINDOWS, sign=1.0, ctx=ctx, results_dir=RESULTS_DIR, figures_dir=STUDY_DIR / "figures", figure_name="vol_reversal.png", title="vol reversal: high recent straddle P&L sold")
    print(summary_df.select(SUMMARY_COLUMNS))


if __name__ == "__main__":
    main()
