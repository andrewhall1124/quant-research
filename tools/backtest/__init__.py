"""A shared backtesting framework for option strategies.

Data flows one way: `data_store/` -> `data_access_layer/` -> here -> a study
under `research/<topic>/`. Nothing in this package writes to `data_store/`;
its own caches live under `tools/backtest/results/`.

The layering is what makes a parameter grid affordable. Layer 1 (`panel.py`)
reads the 8.5 GB greeks store once and caches a tight candidate panel plus
targeted marks. Layer 2 (everything else) runs entirely in memory in seconds,
so sweeping OI thresholds and holding periods is a loop rather than a re-read.

    from tools.backtest import BacktestConfig, build_context, run
    from tools.backtest.structures import AtmStraddle
    from tools.backtest.signals import VrpSignal

    context = build_context()
    result = run(BacktestConfig(signal=VrpSignal(), structure=AtmStraddle()), context)
"""

from tools.backtest.config import BacktestConfig, BacktestResult, Leg
from tools.backtest.engine import BacktestContext, build_context, build_returns, run

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BacktestContext",
    "Leg",
    "build_context",
    "build_returns",
    "run",
]
