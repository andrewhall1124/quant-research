"""Panels shared by the vol momentum and reversal studies.

The signal itself is a trailing sum of the stored reference returns; the
books also need the point-in-time universe, the stock features (`gap_freq`
for the optimizer) and the sectors. All of it comes from the demo's panel
build, so the studies see exactly what the voltium books see.

Run from the project root:

    uv run python -m research.vol_momentum.panel [--start 2018-07-02 --end 2025-06-30]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import polars as pl

from research.earnings_adjusted_vrp.panel import demo_args, load_demo_module

STUDY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STUDY_DIR / "results"

log = logging.getLogger("vol_momentum")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2018, 7, 2))
    parser.add_argument("--end", type=dt.date.fromisoformat, default=dt.date(2025, 6, 30))
    args = parser.parse_args()
    RESULTS_DIR.mkdir(exist_ok=True)
    panels = load_demo_module().build_panels(demo_args(args.start, args.end, refresh=False))
    panels.reference.panel_df.write_parquet(RESULTS_DIR / "reference.parquet")
    panels.features.panel_df.write_parquet(RESULTS_DIR / "features.parquet")
    panels.sectors_df.write_parquet(RESULTS_DIR / "sectors.parquet")
    panels.market_return_df.write_parquet(RESULTS_DIR / "market_return.parquet")
    pl.DataFrame(
        {"key": ["start", "end", "history_start", "forward_end"], "value": [str(args.start), str(args.end), str(panels.history_start), str(panels.forward_end)]}
    ).write_parquet(RESULTS_DIR / "meta.parquet")
    log.info("panels written to %s", RESULTS_DIR)


if __name__ == "__main__":
    main()
