"""Level book on a time-series IV z-score, as a control for the VRP signal.

    uv run python voltium/demos/iv_zscore_book.py [--start ... --end ...]

Same universe, risk model, optimizer, costs and dates as `level_book.py`;
the only change is the signal: each name's log 60-day ATM IV against its own
trailing 250-session mean and std (`TimeSeriesIVZScoreSignal`). No forecast,
no cross-sectional regression — a name is "rich" when its IV is high for
*itself*, whatever the market and its sector are doing. Outputs are suffixed
`_iv_zscore`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from level_book import build_panels, build_parser, run_book  # noqa: E402
from voltium.providers.signals import TimeSeriesIVZScoreSignal, TimeSeriesZScoreConfig  # noqa: E402


def main() -> None:
    parser = build_parser(__doc__.splitlines()[0])
    parser.add_argument("--window", type=int, default=250, help="trailing sessions for the mean and std")
    args = parser.parse_args()
    panels = build_panels(args)
    signal = TimeSeriesIVZScoreSignal(panels.surface_df, TimeSeriesZScoreConfig(window=args.window))
    run_book(args, panels, signal, tag="iv_zscore")


if __name__ == "__main__":
    main()
